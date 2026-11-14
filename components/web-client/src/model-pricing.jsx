import React from 'react';

const INPUT_OUTPUT_RATES=[['input','Input'],['output','Output']];
const DETAIL_CACHE_RATES=[['cache_read','Cache read'],['cache_write_5m','Cache write (5 minutes)'],['cache_write_1h','Cache write (1 hour)']];
const CORE_CACHE_RATES=[['cache_read','Cache read'],['cache_write','Cache write']];
const PROMPT_BASIS='input_tokens+cache_read_input_tokens+cache_creation_input_tokens';

function isRecord(value){return !!value&&typeof value==='object'&&!Array.isArray(value)}
function expandExponent(value){
 const match=/^(\d+)(?:\.(\d+))?e([+-]?\d+)$/i.exec(value);
 if(!match)return value;
 const digits=match[1]+(match[2]||''),point=match[1].length+Number(match[3]);
 if(point<=0)return `0.${'0'.repeat(-point)}${digits}`;
 if(point>=digits.length)return `${digits}${'0'.repeat(point-digits.length)}`;
 return `${digits.slice(0,point)}.${digits.slice(point)}`;
}
function decimalValue(value,allowNumber=false){
 if(typeof value==='number'&&allowNumber&&Number.isFinite(value)&&value>=0)return expandExponent(String(value));
 return typeof value==='string'&&/^\d+(?:\.\d+)?$/.test(value)?value:null;
}
function formatDecimal(value){
 const [whole,fraction='']=value.split('.');
 const integer=whole.replace(/^0+(?=\d)/,'').replace(/\B(?=(\d{3})+(?!\d))/g,',');
 const trimmed=fraction.replace(/0+$/,'');
 return integer+(trimmed?'.'+trimmed:'');
}
function formatRate(value){return value==null?'Unavailable':`$${formatDecimal(value)}`}
function formatCount(value){return Number.isSafeInteger(value)&&value>=0?String(value).replace(/\B(?=(\d{3})+(?!\d))/g,','):null}
function humanizeIdentifier(value){
 const words=String(value||'').replace(/([a-z0-9])([A-Z])/g,'$1 $2').replace(/[_-]+/g,' ').trim().toLowerCase();
 return words?words.split(/\s+/).map(word=>word==='us'?'US':word).join(' ').replace(/^./,letter=>letter.toUpperCase()):'Unavailable';
}
function rateMap(source,fields,allowNumber){
 return Object.fromEntries(fields.map(([key])=>[key,decimalValue(source?.[key],allowNumber)]));
}
function hasRates(rates){return Object.values(rates).some(value=>value!=null)}
function normalizeModifiers(value){
 if(value==null)return {state:'none',entries:[]};
 if(!isRecord(value))return {state:'unavailable',entries:[]};
 const entries=Object.entries(value).map(([key,factor])=>({label:humanizeIdentifier(key),factor:decimalValue(factor)}));
 return entries.length?{state:'provided',entries}:{state:'none',entries:[]};
}
function unavailable(){return {kind:'unavailable'}}
function detailedPricing(details){
 if(!isRecord(details)||details.version!==1||!['fixed','tiered'].includes(details.kind)||details.currency!=='USD'||details.unit!=='per_million_tokens'||details.basis!=='standard_global')return unavailable();
 const base={kind:details.kind,basis:details.basis,modifiers:normalizeModifiers(details.modifiers)};
 if(details.kind==='fixed'){
  const rates=rateMap(details.rates,INPUT_OUTPUT_RATES.concat(DETAIL_CACHE_RATES),false);
  return hasRates(rates)?{...base,rates,cacheRates:DETAIL_CACHE_RATES}:unavailable();
 }
 if(!Number.isSafeInteger(details.threshold_tokens)||details.threshold_tokens<=0||details.threshold_basis!==PROMPT_BASIS||
  !Array.isArray(details.tiers)||details.tiers.length!==2)return unavailable();
 const threshold=formatCount(details.threshold_tokens);
 const [lower,upper]=details.tiers;
 if(!isRecord(lower)||!isRecord(upper)||lower.up_to!==details.threshold_tokens||lower.above!=null||
  upper.above!==details.threshold_tokens||upper.up_to!=null)return unavailable();
 const tiers=details.tiers.map((tier,index)=>{
  const rates=rateMap(tier?.rates,INPUT_OUTPUT_RATES.concat(DETAIL_CACHE_RATES),false);
  const upTo=formatCount(tier?.up_to),above=formatCount(tier?.above);
  const label=threshold&&upTo===threshold&&above==null?`Up to ${threshold} prompt tokens`:
   threshold&&above===threshold&&upTo==null?`Above ${threshold} prompt tokens`:
   `Prompt tier ${index+1} · threshold unavailable`;
  return {label,rates};
 });
 return tiers.some(tier=>hasRates(tier.rates))?{...base,tiers,cacheRates:DETAIL_CACHE_RATES,thresholdBasis:details.threshold_basis}:unavailable();
}
export function modelPricing(model){
 if(!isRecord(model))return unavailable();
 if(model.pricing_details!=null)return detailedPricing(model.pricing_details);
 const flat=model.pricing;
 if(!isRecord(flat)||flat.currency!=='USD')return unavailable();
 const rates=rateMap({input:flat.input_per_million,output:flat.output_per_million,
  cache_read:flat.cache_read_per_million,cache_write:flat.cache_write_per_million},
 [...INPUT_OUTPUT_RATES,...CORE_CACHE_RATES],true);
 return hasRates(rates)?{kind:'fixed',basis:null,modifiers:{state:'none',entries:[]},rates,cacheRates:CORE_CACHE_RATES}:unavailable();
}

function RateList({rates,fields}){
 return <dl className="a-model-price-rates">{fields.map(([key,label])=><div key={key}><dt>{label}</dt><dd>{formatRate(rates?.[key])}</dd></div>)}</dl>;
}
function CacheDetails({pricing}){
 const groups=pricing.kind==='tiered'?pricing.tiers:[{label:null,rates:pricing.rates}];
 return <details className="a-model-pricing-details">
  <summary>Cache rates and modifiers</summary>
  {groups.map((tier,index)=><section key={index}>{tier.label&&<h5>{tier.label}</h5>}<RateList rates={tier.rates} fields={pricing.cacheRates}/></section>)}
  <section><h5>Pricing modifiers</h5>{pricing.modifiers.state==='provided'?
   <dl className="a-model-price-rates">{pricing.modifiers.entries.map(({label,factor})=><div key={label}><dt>{label} modifier</dt><dd>{factor==null?'Unavailable':`×${formatDecimal(factor)}`}</dd></div>)}</dl>:
   <p>{pricing.modifiers.state==='unavailable'?'Pricing modifiers unavailable.':'No pricing modifiers are listed.'}</p>}</section>
 </details>;
}
export function ModelPricing({model}){
 const pricing=modelPricing(model);
 if(pricing.kind==='unavailable')return <p className="a-model-pricing a-model-pricing-unavailable">Pricing unavailable for the selected model.</p>;
 const label=String(model?.name||model?.id||'Selected model');
 const basis=pricing.basis?` · ${humanizeIdentifier(pricing.basis)}`:'';
 return <section className="a-model-pricing" aria-label={`Pricing for ${label}`}>
  <div>
   <p><strong>Listed price for {label}</strong>{basis} · USD per 1M tokens</p>
   {pricing.kind==='fixed'?<RateList rates={pricing.rates} fields={INPUT_OUTPUT_RATES}/>:
    <>
     <ol>{pricing.tiers.map((tier,index)=><li key={index}><p>{tier.label}</p><RateList rates={tier.rates} fields={INPUT_OUTPUT_RATES}/></li>)}</ol>
     <p className="a-model-pricing-note">The prompt size selects the rates for the whole request, including output tokens. Prompt size = fresh input + cache reads + cache writes, counted once.</p>
    </>}
  </div>
  <CacheDetails pricing={pricing}/>
 </section>;
}