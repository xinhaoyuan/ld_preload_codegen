'''
A code generator for LD_PRELOAD wrapper.

Author: Xinhao Yuan <xinhaoyuan@gmail.com>

Generates a standalone GCC project for instrumenting function by
symbol names (with optional versions).
'''

import os, sys
from typing import Union

HEADER_PROLOGUE_TEMPLATE = '''\
#ifndef __{namespace}_inst_h__
#define __{namespace}_inst_h__

#ifdef __cplusplus
extern "C" {{
#endif

{includes}

void {namespace}_sys_init(void);
void {namespace}_sys_thread_init(void);
void {namespace}_sys_inst_on(void);
void {namespace}_sys_inst_off(void);

'''

HEADER_FINALE_CONTENT = '''\
#ifdef __cplusplus
}
#endif
#endif
'''

ENTRY_FUNC_TEMPLATE = '''\
{return_type} {entry_func_name}({args_decl_list}) {{
  {namespace}_sys_try_init();
  if ({namespace}_sys_inst_on) {{
    {namespace}_sys_flag_inst_on = 0;
    {save_return_value}{inst_func_name}({args_call_list});
    {namespace}_sys_flag_inst_on = 1;
    {return_saved_value}
  }}
  else {{
    {return_if_needed}{original_var_name}({args_call_list});
  }}
}}
'''

SOURCE_PROLOGUE_TEMPLATE = '''\
#define _GNU_SOURCE
#include <dlfcn.h>
#include <pthread.h>

#include "{header_filename}"

static          volatile int {namespace}_sys_flag_init = 0;
static __thread volatile int {namespace}_sys_flag_thread_init = 0;
static __thread volatile int {namespace}_sys_flag_inst_on = 0;

{original_vars}

void {namespace}_sys_inst_on(void) {{ {namespace}_sys_flag_inst_on = 1; }}
void {namespace}_sys_inst_off(void) {{ {namespace}_sys_flag_inst_on = 0; }}
int  {namespace}_sys_inst_save(int nr) {{ int r = {namespace}_sys_flag_inst_on; {namespace}_sys_flag_inst_on = nr; return r; }}
void {namespace}_sys_inst_restore(int r) {{ {namespace}_sys_flag_inst_on = r; }}
void {namespace}_sys_try_init() {{
  int r = {namespace}_sys_inst_save(0);

  int _{namespace}_sys_flag_init;
  __atomic_load(&{namespace}_sys_flag_init, &_{namespace}_sys_flag_init, __ATOMIC_ACQUIRE);

  while (_{namespace}_sys_flag_init != 2) {{
    asm volatile("pause\\n": : :"memory");
    _{namespace}_sys_flag_init = 0;
    int cas = __atomic_compare_exchange_n(&{namespace}_sys_flag_init, &_{namespace}_sys_flag_init, 1, 1, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE);
    if (cas) break;
  }}

  if (_{namespace}_sys_flag_init == 2)
    goto skip_global;

  {init_original_body_indented[2]}

  {namespace}_sys_init();
  __atomic_store_n(&{namespace}_sys_flag_init, 2, __ATOMIC_RELEASE);

skip_global:

  if ({namespace}_sys_flag_thread_init == 0) {{
    {namespace}_sys_flag_thread_init = 1;
    {namespace}_sys_thread_init();
  }}
  {namespace}_sys_inst_restore(r);
}}
'''

MAKEFILE_TEMPLATE = '''\
.PHONY: all

CC ?= gcc
CCFLAGS ?= -O2

all: inst.so

{basename}.so: {basename}.c {basename}.h
	${{CC}} ${{CCFLAGS}} -shared -fPIC -lpthread {version_options} -o $@ {basename}.c
'''

class StringIndenter:
    def __init__(self, contents):
        self._content = contents
        pass

    def __getitem__(self, key):
        assert(type(key) == int)
        return self._content.replace('\n', '\n' + ' ' * key)

class CodeGen:

    def __init__(self, namespace : str):
        self._namespace = namespace
        self._includes = []
        self._header_body = ''
        self._source_body = ''
        self._original_vars = ''
        self._init_original_body = ''
        self._version_to_id_map = {}
        self._versioned_functions = {}
        self._typedefs = {}
        pass

    def _get_version_id(self, version : str) -> str:
        if version in self._version_to_id_map:
            return self._version_to_id_map[version]
        else:
            ret = len(self._version_to_id_map)
            self._version_to_id_map[version] = ret
            return ret
        pass

    def _get_internal_name(self, original_name : str, version : Union[None, str]) -> str:
        if version is None:
            return original_name + '__'
        else:
            return original_name + '_' + str(self._get_version_id(version))
        pass

    def _get_original_var_decl(self, prototype : [(str, str)], name : str) -> str:
        assert(len(prototype) > 0)
        args_decl_list = []
        for arg_index in range(1, len(prototype)):
            args_decl_list.append('{} {}'.format(prototype[arg_index][0], prototype[arg_index][1]))
        return '{return_type}(*{name})({args_list})'.format(return_type = prototype[0][0], name = name, args_list = ', '.join(args_decl_list))

    def _get_original_var_call(self, prototype : [(str, str)], name : str) -> str:
        assert(len(prototype) > 0)
        return '{name}({args_list})'.format(name = name, args_list = ', '.join([arg_pair[1] for arg_pair in prototype[1:]]))

    def _get_inst_func_decl(self, prototype : [(str, str)], name : str) -> str:
        assert(len(prototype) > 0)
        args_decl_list = []
        for arg_index in range(1, len(prototype)):
            args_decl_list.append('{} {}'.format(prototype[arg_index][0], prototype[arg_index][1]))
        return '{return_type} {name}({args_list})'.format(return_type = prototype[0][0], name = name, args_list = ', '.join(args_decl_list))

    def _get_internal_type(self, type_decl : str) -> str:
        if '(*)' not in type_decl:
            return type_decl
        if type_decl in self._typedefs:
            return self._typedefs[type_decl]
        index = len(self._typedefs)
        internal_type = '{}_internal_type_{}'.format(self._namespace, index)
        self._header_body += 'typedef {};\n'.format(type_decl.replace('(*)', '(*{})'.format(internal_type), 1))
        self._typedefs[type_decl] = internal_type
        return internal_type

    def add_include(self, include_file : str):
        self._includes.append(include_file)
        pass

    def add_function(self, prototype : [(str, str)], includes : [str] = [], inst_name : Union[None, str] = None, version_label : Union[None, str] = None):
        assert(len(prototype) > 0)
        for index in range(len(prototype)):
            prototype[index] = (self._get_internal_type(prototype[index][0]), prototype[index][1])
            pass

        if version_label is None:
            version = None
        else:
            version = version_label.replace('@', '')
        internal_name = self._get_internal_name(prototype[0][1], version) if inst_name is None else inst_name
        original_var_name = '{namespace}_orig_{internal_name}'.format(
            namespace = self._namespace,
            internal_name = internal_name)
        entry_func_name = '{namespace}_entry_{internal_name}'.format(
            namespace = self._namespace,
            internal_name = internal_name)
        inst_func_name = '{namespace}_inst_{internal_name}'.format(
            namespace = self._namespace,
            internal_name = internal_name)

        self._header_body += 'extern {};\n'.format(self._get_original_var_decl(prototype, original_var_name))
        self._header_body += '{};\n'.format(self._get_inst_func_decl(prototype, inst_func_name))
        self._original_vars += '{} = (void*)0;\n'.format(self._get_original_var_decl(prototype, original_var_name))

        if version is None:
            lookup_call = 'dlsym(RTLD_NEXT, "{original_name}")'.format(
                original_name = prototype[0][1])
        else:
            lookup_call = 'dlvsym(RTLD_NEXT, "{original_name}", "{version}")'.format(
                original_name = prototype[0][1],
                version = version)
        self._init_original_body += '{original_var} = {lookup_call};\n'.format(
            original_var = original_var_name,
            lookup_call = lookup_call)

        self._source_body += ENTRY_FUNC_TEMPLATE.format(
            return_type = prototype[0][0],
            namespace = self._namespace,
            original_var_name = original_var_name,
            entry_func_name = entry_func_name,
            inst_func_name = inst_func_name,
            args_decl_list = ', '.join(['{} {}'.format(arg_pair[0], arg_pair[1]) for arg_pair in prototype[1:]]),
            args_call_list = ', '.join([arg_pair[1] for arg_pair in prototype[1:]]),
            save_return_value = '{return_type} {namespace}_ret_value = '.format(
                return_type = prototype[0][0], namespace = self._namespace) if prototype[0][0] != 'void' else '',
            return_saved_value = 'return {namespace}_ret_value;'.format(
                return_type = prototype[0][0], namespace = self._namespace) if prototype[0][0] != 'void' else '',
            return_if_needed = '' if prototype[0][0] == 'void' else 'return ',
        )

        if version is not None:
            self._source_body += '__asm__(".symver {entry_func_name}, {symbol_name}");'.format(
                entry_func_name = entry_func_name,
                symbol_name = '{}@{}'.format(prototype[0][1], version_label))
            if version not in self._versioned_functions:
                self._versioned_functions[version] = []
                pass
            self._versioned_functions[version].append(prototype[0][1])

        pass

    def get_header_contents(self) -> str:
        return '{prologue}\n{body}\n{finale}'.format(
            prologue = HEADER_PROLOGUE_TEMPLATE.format(
                namespace = self._namespace,
                includes = '\n'.join(['#include {}'.format(include_file) for include_file in self._includes])),
            body = self._header_body,
            finale = HEADER_FINALE_CONTENT)

    def get_source_contents(self, header_filename : str) -> str:
        return '{prologue}\n{body}'.format(
            prologue = SOURCE_PROLOGUE_TEMPLATE.format(
                namespace = self._namespace,
                header_filename = header_filename,
                original_vars = self._original_vars,
                init_original_body_indented = StringIndenter(self._init_original_body)
                ),
            body = self._source_body)

    def get_version_contents(self) -> str:
        contents = ''
        for v in self._versioned_functions:
            contents += '{version} {{ global : {functions}; }};\n'.format(
                version = v,
                functions = '; '.join(self._versioned_functions[v]))
            pass
        return contents

    def generate(self, output_dir : str, basename : str = 'inst', version_filename : str = 'version.txt', makefile : bool = True):
        try:
            os.makedirs(output_dir)
        except FileExistsError:
            pass
        except:
            raise
        header_filename = '{}.h'.format(basename)
        source_filename = '{}.c'.format(basename)
        with open(os.path.join(output_dir, header_filename), 'w') as f:
            f.write(self.get_header_contents())
            pass
        with open(os.path.join(output_dir, source_filename), 'w') as f:
            f.write(self.get_source_contents(header_filename))
            pass
        version_contents = self.get_version_contents()
        if len(version_contents) > 0:
            with open(os.path.join(output_dir, version_filename), 'w') as f:
                f.write(version_contents)
                pass
            has_version = True
            pass
        if makefile:
            with open(os.path.join(output_dir, 'Makefile'), 'w') as f:
                f.write(MAKEFILE_TEMPLATE.format(
                    basename = basename,
                    version_options = '' if not has_version else '-Wl,--version-script -Wl,{}'.format(version_filename)))
                pass
            pass
        pass

    pass

def run_tests():
    code_gen = CodeGen('ns')
    code_gen.add_include('<stdio.h>')
    code_gen.add_function([('int', 'x'), ('int', 'y'), ('int', 'z')])
    code_gen.add_function([('void(*)(int)', 'foo'), ('int', 'bar')], version_label = '@GLIBC_2.0')
    code_gen.generate('out')
    pass

if __name__ == '__main__':
    run_tests()
