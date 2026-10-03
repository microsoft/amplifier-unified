const {DistributionUpdateOwner}=await import(process.env.DISTRIBUTION_OWNER_MODULE);
const [phase,directory]=process.argv.slice(2);let effects=0;
const fence={purpose:'recovery',fenceId:'fence',commandId:'reset',instanceId:'instance',dataScope:'fixture'};
const denied=async()=>{effects++;throw Error('unexpected ordinary work');};
const owner=new DistributionUpdateOwner({directory,dataScope:'fixture',preferences:{autoCheck:true,autoInstall:true,intervalMs:23456},releases:{check:denied,prepare:denied,verify:denied},lifecycle:{inspect:denied,admitRestart:denied,restart:denied},verifyRecoveryFence:async()=>{}});
let result;
if(phase==='apply'){
 const review=(await owner.appReset.perform('prepare',{commandId:'prepare',parts:['updates.preferences'],privateContentReviewed:true},fence)).receipt.result;
 result=await owner.appReset.perform('apply',{commandId:'apply',preparedId:review.preparedId,reviewHash:review.reviewHash},fence);
}else result={...await owner.appReset.perform('inspect',{commandId:'apply'}),preferences:owner.inspect().preferences,effects};
process.stdout.write(JSON.stringify(result),()=>process.exit(0)); // Intentionally no clean owner.close().
