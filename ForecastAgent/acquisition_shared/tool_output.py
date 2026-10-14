"""Bound metadata in tool replies; complete coordinate records stay archived."""
import copy


def compact_view(value):
    child=copy.deepcopy(value)
    def visit(obj):
        if isinstance(obj,list):
            for item in obj:visit(item)
        elif isinstance(obj,dict):
            if 'sheet_cell_view' in obj:
                cells=obj.pop('sheet_cell_view')
                obj['saved_cell_view_available']=True
                obj['saved_cell_view_source_sha256']=cells.get('raw_source_sha256')
            for key in ('month_literal_cells','original_row_numbers','tabs'):
                if isinstance(obj.get(key),list):
                    items=obj[key]
                    limit=8 if key=='tabs' else 12
                    if len(items)>limit:
                        obj[key+'_count']=len(items)
                        obj[key+'_projection_truncated']=True
                        obj[key]=items[:limit] if key=='tabs' else items[:6]+items[-6:]
            for item in list(obj.values()):visit(item)
    visit(child)
    return child
