#!/usr/bin/env python3

import sys
import getopt
import json
import ast

USAGE = """\
Usage: {0} -f input.json
""".format(sys.argv[0])

def handle_func_entry(output, global_options, func_entry):
    if not isinstance(func_entry, dict):
        raise Exception('Except a dict as the function entry')

    if 'name' not in func_entry:
        raise Exception('name is required in func_entry')
    elif 'ret_type' not in func_entry:
        raise Exception('ret_type is required in func_entry')
    elif 'args' not in func_entry or\
         not isinstance(func_entry['args'], list):
        raise Exception('args is required list in func_entry')
    elif 'opts' in func_entry and\
         not isinstance(func_entry['opts'], list):
        raise Exception('opts in func_entry should be a list')
    elif 'incl' in func_entry and\
         not isinstance(func_entry['incl'], list):
        raise Exception('incl in func_entry should be a list')

    if 'incl' in func_entry:
        for i in func_entry['incl']:
            if not isinstance(i, str):
                raise Exception('incl in func_entry should contains strings')
            output['interposition_incl_set'].add(i)

    name = func_entry['name']
    ret_type = func_entry['ret_type']
    opts = set()
    if 'opts' in func_entry:
        for o in func_entry['opts']:
            if not isinstance(o, str):
                raise Exception('opts in func_entry should contains strings')
            opts.add(o)

    args_decl = ''
    args_invk = ''
    for i, a in enumerate(func_entry['args']):
        if not isinstance(a, list) or\
           len(a) != 2 or\
           not isinstance(a[1], str):
            raise Exception('item of args should be [ type, name ]')
        if '(*)' in a[0]:
            args_decl += '{0}{1}'.format('' if i == 0 else ', ', a[0].replace('(*)', '(*{})'.format(a[1])))
        else:
            args_decl += '{0}{1} {2}'.format('' if i == 0 else ', ', a[0], a[1])
        args_invk += '{0}{2}'.format('' if i == 0 else ', ', a[0], a[1])

    if '(*)' in ret_type:
        func_header = '{0}({1}) {{'.format(ret_type.replace('(*)', '(*{})'.format(name)),  args_decl)
    else:
        func_header = '{0} {1}({2}) {{'.format(ret_type, name, args_decl)
    output['interposition_source_body'] += \
        """{3}
    if ({0}_use_inst) {{
        {0}_use_inst = 0; {0}_inst_{1}({2}); {0}_use_inst = 1;
    }}
    else
        {0}_orig_{1}({2});
}}""".format(global_options['namespace'], name, args_invk, func_header)

    output['interposition_header_body'] += \
        'extern {2} (*{0}_orig_{1})({3});\n'.format(
            global_options['namespace'],
            name,
            ret_type,
            args_decl)
    output['interposition_header_body'] += \
        'extern {2} ({0}_inst_{1})({3});\n'.format(
            global_options['namespace'],
            name,
            ret_type,
            args_decl)


    output['interposition_source_header'] += \
        '{2} (*{0}_orig_{1})({3}) = (void *)0;\n'.format(
            global_options['namespace'],
            name,
            ret_type,
            args_decl)
    
    output['interposition_source_init_func_body'] += \
        '    {0}_orig_{1} = dlsym(RTLD_NEXT, "{1}");\n'.format(
            global_options['namespace'],
            name)
    

def handle_main_entry(output, main_entry):
    if not isinstance(main_entry, dict):
        raise Exception('expect a list as the main entry')
    elif 'functions' not in main_entry:
        raise Exception('functions list is required in the main entry')
    elif 'namespace' in main_entry and \
       not isinstance(main_entry['namespace'], str):
        raise Exception('except namespace in main entry to be a string')
    elif 'incl' in main_entry and\
         not isinstance(main_entry['incl'], list):
        raise Exception('incl in main_entry should be a list')
    elif 'header_filename' not in main_entry or\
         not isinstance(main_entry['header_filename'], str):
        raise Exception('header_filename in main_entry should be a string')
    elif 'source_filename' not in main_entry or\
         not isinstance(main_entry['source_filename'], str):
        raise Exception('source_filename in main_entry should be a string')

    global_options = {}
    global_options['namespace'] \
        = main_entry['namespace'] if 'namespace' in main_entry else ''
    global_options['header_filename'] = main_entry['header_filename']
    global_options['source_filename'] = main_entry['source_filename']

    output['interposition_header_body'] = """\
void {0}_inst_on(void);
void {0}_inst_off(void);
int  {0}_inst_save(void);
void {0}_inst_restore(int);
void {0}_inst_init(void);

""".format(global_options['namespace'])

    output['interposition_incl_set'] = set()
    if 'incl' in main_entry:
        for i in main_entry['incl']:
            if not isinstance(i, str):
                raise Exception('incl in main_entry should contains strings')
            output['interposition_incl_set'].add(i)

    output['interposition_source_header'] = ''
    output['interposition_source_init_func_body'] = ''
    output['interposition_source_body'] = ''

    for func_entry in main_entry['functions']:
        handle_func_entry(output, global_options, func_entry)

    header_output  = """\
#ifndef __{0}_INTERPOSITION_H__
#define __{0}_INTERPOSITION_H__

#if __cplusplus
extern "C" {{
#endif
""".format(global_options['namespace'])
    header_output += '\n'.join(
        [ '#include {0}'.format(i) for i in output['interposition_incl_set'] ])
    header_output += '\n\n' + output['interposition_header_body']
    header_output += """
#if __cplusplus
}
#endif
#endif
"""

    source_output  = """\
#define _GNU_SOURCE
#include <dlfcn.h>
#include "{0}"
""".format(global_options['header_filename'])
    source_output += """
{1}
static __thread int {0}_use_inst = 0;

void {0}_inst_on(void) {{ {0}_use_inst = 1; }}
void {0}_inst_off(void) {{ {0}_use_inst = 0; }}
int  {0}_inst_save(void) {{ int r = {0}_use_inst; {0}_use_inst = 0; return r; }}
void {0}_inst_restore(int r) {{ {0}_use_inst = r; }}
void {0}_inst_init(void) {{
{2}}}

{3}""".format(global_options['namespace'],
            output['interposition_source_header'],
            output['interposition_source_init_func_body'],
            output['interposition_source_body'])

    with open(global_options['header_filename'], 'w') as f:
        f.write(header_output)
    
    with open(global_options['source_filename'], 'w') as f:
        f.write(source_output)
    

def main(argv):
    opts, args = getopt.getopt(argv[1:], 'hf:a')
    input_file = sys.stdin
    use_ast = False
    
    try:
        for name, value in opts:
            if name == '-h':
                sys.stderr.write(USAGE)
            elif name == '-f':
                input_file = open(value)
            elif name == '-a':
                use_ast = True
    except Exception as x:
        sys.stderr.write('Error while handling options: {}\n'.format(x))
        return

    try:
        if use_ast:
            data = ast.literal_eval(input_file.read())
        else:
            data = json.load(input_file)
    except Exception as x:
        sys.stderr.write('Error while parse input as {}: {}\n'.format("AST" if use_ast else "JSON", x))
        return

    output = {}
    handle_main_entry(output, data)
    
if __name__ == '__main__':
    main(sys.argv)
