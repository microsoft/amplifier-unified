import type {Json} from './types.js';
const id={type:'string',minLength:1,maxLength:200};
const job={type:'string',pattern:'^[a-f0-9-]{36}$'};
const hash={type:'string',pattern:'^[a-f0-9]{64}$'};
const selection={type:'array',minItems:1,maxItems:32,uniqueItems:true,items:{type:'string',pattern:'^ahp-session:/[^/?#]+$',maxLength:300}};
const page={cursor:{type:'string',maxLength:200},limit:{type:'integer',minimum:1,maximum:50}};
const define=(description:string,properties:Json,required:string[]=[])=>({description,schema:{type:'object',properties,required,additionalProperties:false}});
export const definitions:Record<string,{description:string;schema:Json}>={
 'recovery.list':define('List this account’s bounded recovery jobs. No native histories are scanned.',page),
 'recovery.job':define('Read one exact recovery job, including explicit incomplete or unknown outcomes.',{jobId:job},['jobId']),
 'recovery.command':define('Recover the exact original command without repeating it.',{commandId:id},['commandId']),
 'recovery.preview':define('Read the selected prepared manifest and exclusions in bounded pages.',{jobId:job,...page,cursor:{type:'string',maxLength:16384}},['jobId']),
 'recovery.prepare':define('Prepare an immutable review of explicitly selected native files after coordinated quiescence. Contains private content; this is not a full-product backup.',{sessions:selection,parts:{type:'array',minItems:1,maxItems:3,uniqueItems:true,items:{enum:['session-history','session-state','native-settings']}},privateContentReviewed:{const:true},includeCredentials:{type:'boolean'},credentialsReviewed:{type:'boolean'}},['sessions','parts','privateContentReviewed']),
 'recovery.archive.prepare':define('Prepare a paged immutable native-only archive review with explicitly selected shared/workspace configuration. Requires stopped external writers for configuration trees; never scans unselected history.',{sessions:selection,workspaceConfigurationFor:{...selection,minItems:0},parts:{type:'array',minItems:1,maxItems:4,uniqueItems:true,items:{enum:['session-history','session-state','shared-configuration','workspace-configuration']}},privateContentReviewed:{const:true},includeCredentials:{type:'boolean'},credentialsReviewed:{type:'boolean'}},['sessions','parts','privateContentReviewed']),
 'recovery.snapshot':define('Capture exactly the reviewed selected native files into a private archive. Changed source requires a fresh review.',{preparedJobId:job,previewHash:hash},['preparedJobId','previewHash']),
 'recovery.reset.prepare':define('Review a reversible reset of one selected session’s noncanonical configuration; history, tasks and ownership remain preserved.',{sessionId:{type:'string',pattern:'^ahp-session:/[^/?#]+$',maxLength:300},scope:{const:'session-configuration'},privateContentReviewed:{const:true}},['sessionId','scope','privateContentReviewed']),
 'recovery.reset.apply':define('Apply exactly the reviewed noncanonical reset, retaining originals. No automatic session restart.',{preparedJobId:job,previewHash:hash},['preparedJobId','previewHash']),
 'recovery.reset.restore':define('Restore retained configuration only when the exact reviewed post-reset state is unchanged.',{preparedJobId:job,previewHash:hash,resetJobId:job,expectedPostResetHash:hash},['preparedJobId','previewHash','resetJobId','expectedPostResetHash']),
 'recovery.reconcile':define('Inspect original native and host receipts only; release intake solely when previously persisted conclusive release evidence exists. Never repeats snapshot or reset.',{jobId:job},['jobId']),
};

/** Preserve the baseline contract unless this exact configured native peer negotiated additions. */
export function negotiatedDefinitions(native:Json|undefined,destinations:{id:string;label:string}[]){
 const result:typeof definitions=structuredClone(definitions),plans=native?.version===1?native.archivePlans:undefined;
 const properties=result['recovery.archive.prepare'].schema.properties,base=properties.parts.items.enum as string[];
 const stores=['native-import-records','native-preference-receipts','native-maintenance-records','native-retained-archives'];
 if(plans?.nativeStores?.version===1&&Array.isArray(plans.nativeStores.parts))for(const part of stores)if(plans.nativeStores.parts?.includes(part))base.push(part);
 if(plans?.fullNative?.version===1&&plans.fullNative.part==='full-native-authority')base.push('full-native-authority');
 properties.parts.maxItems=base.length;if(base.length>4)properties.sessions.minItems=0;
 result['recovery.archive.prepare'].description='Prepare an immutable paged native archive review. Full-native inventory is explicit and native-only; authoritative omissions prevent complete coverage.';
 const restore=plans?.fullNative?.restore;
 if(plans?.fullNative?.version===1&&restore?.version===1&&restore.newDestinationOnly===true&&restore.passiveInspect===true&&destinations.length){
  result['recovery.restore.prepare']=define('Verify an exact successful native archive and review restoration into a new owned destination. Existing data is never overwritten; no inputs execute.',{snapshotJobId:job,sha256:hash,destination:{type:'object',properties:{rootId:{type:'string',enum:destinations.map(row=>row.id)},name:{type:'string',pattern:'^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$'}}},privateContentReviewed:{const:true},credentialsReviewed:{type:'boolean'}},['snapshotJobId','sha256','destination','privateContentReviewed']);
  result['recovery.restore.prepare'].schema.properties.destination.required=['rootId','name'];result['recovery.restore.prepare'].schema.properties.destination.additionalProperties=false;
  result['recovery.restore.apply']=define('Restore exactly the reviewed native archive into its new absent destination. Original files and uncertain work remain preserved; configuration review and runtime qualification remain required.',{preparedJobId:job,previewHash:hash},['preparedJobId','previewHash']);
 }
 for(const [name,row] of Object.entries(result))if(name.startsWith('recovery.restore.'))row.schema.properties.sessionId={type:'string',pattern:'^ahp-session:/[^/?#]+$',maxLength:300};
 return result;
}

// Optional routing selector lets the trusted native bridge retain own-session context.
for(const [name,definition] of Object.entries(definitions))if(name!=='recovery.list')definition.schema.properties.sessionId??={type:'string',pattern:'^ahp-session:/[^/?#]+$',maxLength:300};
export const quiescenceAccess:Record<string,'read'|'reconcile'>={
 'recovery.list':'read','recovery.job':'read','recovery.command':'read','recovery.preview':'read','recovery.reconcile':'reconcile',
};
/** Validate our small public schema vocabulary before persisting any command. */
export function validate(schema:Json,value:any,path='args'):void{
 if(schema.const!==undefined&&value!==schema.const)throw Error(`${path} requires the advertised value`);
 if(schema.enum&&!schema.enum.includes(value))throw Error(`${path} is not an advertised value`);
 if(schema.type==='object'){
  if(!value||typeof value!=='object'||Array.isArray(value))throw Error(`${path} must be an object`);
  for(const key of schema.required??[])if(!(key in value))throw Error(`${path}.${key} is required`);
  for(const key of Object.keys(value)){if(!Object.hasOwn(schema.properties,key))throw Error(`${path}.${key} is not permitted`);validate(schema.properties[key],value[key],`${path}.${key}`);}
 }
 if(schema.type==='array'){
  if(!Array.isArray(value)||value.length<schema.minItems||value.length>schema.maxItems)throw Error(`${path} requires a bounded array`);
  if(schema.uniqueItems&&new Set(value.map(v=>JSON.stringify(v))).size!==value.length)throw Error(`${path} has duplicate entries`);
  value.forEach((v,i)=>validate(schema.items,v,`${path}[${i}]`));
 }
 if(schema.type==='string'&&(typeof value!=='string'||value.length<(schema.minLength??0)||value.length>(schema.maxLength??10000)||/[\x00-\x1f]/.test(value)||schema.pattern&&!new RegExp(schema.pattern).test(value)))throw Error(`${path} is not a valid bounded identifier`);
 if(schema.type==='boolean'&&typeof value!=='boolean')throw Error(`${path} must be boolean`);
 if(schema.type==='integer'&&(!Number.isSafeInteger(value)||value<schema.minimum||value>schema.maximum))throw Error(`${path} must be a bounded integer`);
}
