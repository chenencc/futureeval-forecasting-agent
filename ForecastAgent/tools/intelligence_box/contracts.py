"""Validate the JSON function envelope before dispatch or reservation."""
def validate_call(tool,arguments,definitions):
    spec=next((d['function'] for d in definitions if d['function']['name']==tool),None)
    if spec is None: raise ValueError('Unknown toolbox function')
    schema=spec['parameters']
    if not isinstance(arguments,dict): raise ValueError('Tool arguments must be a JSON object')
    properties=schema.get('properties',{})
    if set(arguments)-set(properties): raise ValueError('Unknown tool arguments; inspect its function schema')
    if set(schema.get('required',[]))-set(arguments): raise ValueError('Missing required tool arguments')
    types={'string':lambda v:isinstance(v,str),'integer':lambda v:type(v) is int,'object':lambda v:isinstance(v,dict)}
    for key,value in arguments.items():
        p=properties[key]
        if not types[p['type']](value): raise ValueError('Wrong argument type: '+key)
        if 'enum' in p and value not in p['enum']: raise ValueError('Unsupported argument value: '+key)
        if ('minimum' in p and value<p['minimum']) or ('maximum' in p and value>p['maximum']): raise ValueError('Argument outside bounds: '+key)
        if 'maxLength' in p and len(value)>p['maxLength']: raise ValueError('Argument exceeds length: '+key)
