"""Wayback discovery and exact archived replay; two separately budgeted HTTP calls."""
import base64
import json
from datetime import datetime,timezone,timedelta
from urllib.parse import urlencode


def archive_lookup(url,cutoff,fetch):
 if not cutoff: raise ValueError('Archive lookup requires a historical cutoff')
 endpoint='https://web.archive.org/cdx/search/cdx?'+urlencode({'url':url,'output':'json','filter':'statuscode:200',
  'fl':'timestamp,original','to':(cutoff-timedelta(seconds=1)).strftime('%Y%m%d%H%M%S'),'limit':'-1','matchType':'exact'})
 index=fetch(endpoint)
 rows=json.loads(base64.b64decode(index['raw_response_base64']))
 if not isinstance(rows,list) or len(rows)!=2 or rows[0]!=['timestamp','original'] or len(rows[1])!=2:
  raise ValueError('No unambiguous pre-cutoff archive capture found')
 stamp,original=rows[1]
 if original!=url: raise ValueError('Archive URL differs from requested exact URL')
 captured=datetime.strptime(stamp,'%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)
 if captured>=cutoff: raise ValueError('Archive index returned a later capture')
 replay='https://web.archive.org/web/'+stamp+'id_/'+original
 page=fetch(replay)
 if page.get('final_url')!=replay: raise ValueError('Archive replay redirected; timestamp provenance cannot be established')
 page.update(url=url,temporal_status='archive_pre_cutoff_capture',archive_timestamp=captured.isoformat(),
   archive_provenance={'index_url':endpoint,'index_sha256':index['sha256'],'replay_url':replay,
      'limitation':'Archive operator timestamp; not independently notarized. Historical question and model knowledge remain unaudited.'},
   capture_method='wayback_replay',links=[])
 return page
