"""Voice provider accounting without invented prices or transcript duplication."""
import math

def normalize_voice_usage(raw):
    raw=raw if isinstance(raw,dict) else {}
    result={'costType':'unavailable'}
    for source,target in [('input_tokens','inputTokens'),('output_tokens','outputTokens'),('total_tokens','totalTokens')]:
        value=raw.get(source)
        if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0:result[target]=value
    details=raw.get('input_token_details') or raw.get('input_tokens_details') or {}
    if isinstance(details,dict):
        cache=details.get('cached_tokens')
        if isinstance(cache,(int,float)) and math.isfinite(cache) and cache>=0:result['cacheReadTokens']=cache
    if 'totalTokens' not in result and 'inputTokens' in result and 'outputTokens' in result:result['totalTokens']=result['inputTokens']+result['outputTokens']
    cost=raw.get('cost_usd')
    if isinstance(cost,(int,float)) and not isinstance(cost,bool) and math.isfinite(cost) and cost>=0:result.update(costUsd=cost,costType='reported')
    return result
