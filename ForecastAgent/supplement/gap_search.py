"""One physical discovery request per callback; the caller owns reservations."""
from ForecastAgent.providers.tavily_search import search_batch
from ForecastAgent.providers.exa_search import search as exa_search


def callback(tavily_key, exa_key):
    def search(tool, query):
        if tool not in search.available_tools:
            raise ValueError('Discovery credential unavailable; no request sent')
        return (search_batch(query,tavily_key,topic='general') if tool=='tavily' else
                exa_search(query,exa_key,category='general'))
    search.available_tools={name for name,key in [('tavily',tavily_key),('exa',exa_key)] if key}
    return search
