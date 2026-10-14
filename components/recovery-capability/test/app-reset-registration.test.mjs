import test from 'node:test';
import assert from 'node:assert/strict';
import {resetOwners,AppResets} from '../dist/app-reset.js';
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
