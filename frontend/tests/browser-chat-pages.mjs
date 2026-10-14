// Enter the selected chat's current public controls, not a removed Settings page.
export async function openChatAdvancedPage(page,title){
 await page.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
 // The fixture's persisted panel can arrive after the document and toolbar.
 // Wait for that accepted state to mount before inspecting or dismissing it.
 if(await page.evaluate(()=>!!window.amplifier.getState().view?.panel))await page.locator('[role="dialog"]:visible').waitFor();
 const current=page.getByRole('dialog',{name:title,exact:true});
 if(await current.isVisible())return;
 const dialog=page.locator('[role="dialog"]:visible');
 if(await dialog.count())await dialog.getByRole('button',{name:/^Close (panel|settings)$/i}).click();
 await page.getByRole('button',{name:'Chat',exact:true}).click();
 await page.getByRole('group',{name:'Chat',exact:true}).getByRole('button',{name:'Advanced',exact:true}).click();
 await page.getByRole('navigation',{name:'Advanced chat options'}).getByRole('button',{name:new RegExp('^'+title.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'))}).click();
 await current.waitFor({state:'visible'});
}
