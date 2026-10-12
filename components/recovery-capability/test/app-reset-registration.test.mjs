import test from 'node:test';
import assert from 'node:assert/strict';
import {resetOwners,AppResets,publicReview} from '../dist/app-reset.js';
import {negotiatedDefinitions,validate} from '../dist/schemas.js';

const native={appReset:{version:1,retainedUndo:true,requiresRecoveryAdminLease:true,parts:['native.app-bundle-default']}};
const noCall=async()=>{throw Error('Contract discovery cannot invoke an owner');};
const notifications={id:'notifications',parts:['notifications.settings','notifications.credentials'],perform:noCall};
const updates={id:'updates',parts:['updates.preferences'],perform:noCall};

test('registered update preferences extend exact advertised parts without borrowing another owner authority',()=>{
 const oldOwners=resetOwners(native,noCall,[notifications]);
 const oldSchema=negotiatedDefinitions(native,[],new AppResets(oldOwners).parts)['recovery.appReset.prepare'].schema;
 assert.throws(()=>validate(oldSchema,{parts:['updates.preferences'],privateContentReviewed:true}),/advertised/);
 const owners=resetOwners(native,noCall,[notifications,updates]),parts=new AppResets(owners).parts;
 assert.deepEqual(parts,['native.app-bundle-default','notifications.settings','notifications.credentials','updates.preferences']);
 const schema=negotiatedDefinitions(native,[],parts)['recovery.appReset.prepare'].schema;
 validate(schema,{parts,privateContentReviewed:true,credentialsReviewed:true});
 assert.throws(()=>resetOwners(native,noCall,[{...notifications,parts:['updates.preferences']}]),/misowned/);
 assert.throws(()=>resetOwners(native,noCall,[{...updates,parts:['notifications.credentials']}]),/misowned/);
 assert.throws(()=>resetOwners(native,noCall,[updates,updates]),/Invalid registered/);
 assert.throws(()=>resetOwners(native,noCall,[{...updates,id:'__proto__'}]),/Invalid registered/);
 assert.throws(()=>resetOwners(native,noCall,[{...updates,parts:['updates.trusted-keys']}]),/misowned/);
});


test('recovery locations are bounded, owner-shaped, and contain no setting values',()=>{
 const owner={id:'native-app-defaults',parts:['native.app-bundle-default'],perform:noCall};
 const storage={affectedPaths:['/private/app/admin-settings.yaml'],retainedOriginals:{path:'/private/app/maintenance/receipts.sqlite3',table:'app_reset_previews',recordId:'a'.repeat(32)}};
 const review={ownerId:owner.id,preparedId:'a'.repeat(32),reviewHash:'b'.repeat(64),parts:owner.parts,revision:'rev',expiresAt:Date.now()/1000+600,containsPrivateContent:true,credentialsIncluded:false,coverage:'explicit-app-local-parts',preserved:['unselected'],omissions:['shared'],restoresCommandId:null,items:[{part:owner.parts[0],operation:'clear-bundle-active-only'}],storage};
 assert.deepEqual(publicReview(review,owner,owner.parts).storage,storage);
 const {storage:omitted,...legacy}=review;assert.deepEqual(publicReview(legacy,owner,owner.parts),legacy,'existing reviews remain usable');
 for(const bad of [null,{...storage,token:'secret'}, {...storage,affectedPaths:['relative']},{...storage,affectedPaths:['/private/'+String.fromCharCode(0)]},{...storage,affectedPaths:['x'.repeat(5000)]},{...storage,retainedOriginals:{...storage.retainedOriginals,table:'credentials'}},{...storage,retainedOriginals:{...storage.retainedOriginals,recordId:{secret:true}}}])assert.throws(()=>publicReview({...review,storage:bad},owner,owner.parts),/storage locations/);
});
