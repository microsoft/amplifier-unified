export function appearanceComposition(appearance){
 const theme=appearance?.preview?.theme??appearance?.theme??null;
 return {
  id:typeof appearance?.composition?.id==='string'?appearance.composition.id:null,
  theme,
  css:typeof theme?.css==='string'?theme.css:'',
  preferences:appearance?.renderedPreferences&&typeof appearance.renderedPreferences==='object'
   ?appearance.renderedPreferences:{},
 };
}

// Reference is always the first layer; previews and selected library entries
// contribute only their validated delta.
export function composeAppearanceStyles(referenceCss,deltaCss){
 return [referenceCss,deltaCss].filter(value=>typeof value==='string'&&value.length).join('\n');
}
