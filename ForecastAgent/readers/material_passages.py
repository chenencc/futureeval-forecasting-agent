"""Exact saved-text spans with complete lines and bounded table coverage.

Headers remain separate exact spans when a table must be split. Coverage describes
saved rows, never a trading calendar or the completeness of the upstream dataset.
"""
def spans(body, limit=20000):
    lines = []
    offset = 0
    for line in body.splitlines(keepends=True):
        lines.append((offset, offset + len(line), line))
        offset += len(line)
    output = []
    i = 0
    while i < len(lines):
        table = lines[i][2].strip().startswith('|') and lines[i][2].count('|') >= 2
        j = i + 1
        if table:
            while j < len(lines) and lines[j][2].strip().startswith('|'):
                j += 1
        else:
            while j < len(lines) and not lines[j][2].strip().startswith('|') and lines[j][1] - lines[i][0] <= 1800:
                j += 1
        end = j
        cursor = i
        while cursor < end:
            stop = cursor + 1
            while stop < end and lines[stop][1] - lines[cursor][0] <= limit:
                stop += 1
            start, finish = lines[cursor][0], lines[stop-1][1]
            output.append({'start':start, 'end':finish, 'text':body[start:finish],
                'reading':{'kind':'table' if table else 'text', 'complete_lines':True,
                    'complete_saved_table':table and cursor == i and stop == end,
                    'table_start':lines[i][0] if table else None,
                    'table_end':lines[end-1][1] if table else None,
                    'saved_table_line_count':end-i if table else None,
                    'delivered_line_range':[cursor-i,stop-i] if table else None,
                    'header_span': [lines[i][0],lines[min(i+3,end)-1][1]] if table else None,
                    'upstream_completeness_verified':False}})
            cursor = stop
        i = end
    return output
