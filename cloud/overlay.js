// Cloud-specific wording and settings. The local interface stays unchanged.
Object.assign(FR, {
  local:'Application en ligne',
  authHelp:'Connectez-vous avec votre compte du cabinet.',
  authText:'Suivez le stock, les ventes et les commandes depuis vos appareils connectés à Internet.',
  authTag3:'Données en ligne',
  cloudBackup:'Sauvegarde à configurer',
  cloudBackupHelp:'Avant toute utilisation réelle, configurez et testez une sauvegarde indépendante de la base et des pièces jointes.'
});
Object.assign(AR, {
  local:'تطبيق عبر الإنترنت',
  authHelp:'سجّل الدخول باستعمال حساب العيادة.',
  authText:'تابع المخزون والمبيعات والطلبات من أجهزتك المتصلة بالإنترنت.',
  authTag3:'بيانات عبر الإنترنت',
  cloudBackup:'النسخ الاحتياطي يحتاج إلى إعداد',
  cloudBackupHelp:'قبل الاستخدام الفعلي، أعدّ واختبر نسخة احتياطية مستقلة من قاعدة البيانات والمرفقات.'
});
pageSettings = async function(){
  const s=await api('settings'), admin=S.user.role==='admin';
  q('#page').innerHTML=`<div class="grid-two" style="margin-top:0"><div class="stack">
    ${admin?`<section class="panel"><div class="section-head"><h2>${esc(t('clinicDetails'))}</h2></div>
      <form id="settings-form"><div class="form-grid">
      ${field(t('clinicName'),'clinic_name',s.clinic_name,{required:true})}
      ${field(t('clinicPhone'),'clinic_phone',s.clinic_phone)}
      ${field(t('clinicAddress'),'clinic_address',s.clinic_address,{full:true})}
      ${field(t('clinicTaxId'),'clinic_tax_id',s.clinic_tax_id,{full:true})}</div>
      <div class="warning section-gap">${esc(t('taxNotice'))}</div>
      <div class="form-footer"><button class="btn primary" type="submit">${esc(t('save'))}</button></div></form></section>`:''}
    <section class="panel"><div class="section-head"><h2>${esc(t('changePassword'))}</h2></div>
      <form id="password-form"><div class="form-grid">
      ${field(t('currentPassword'),'current_password','',{type:'password',required:true})}
      ${field(t('newPassword'),'new_password','',{type:'password',required:true,help:t('passwordHelp')})}</div>
      <div class="form-footer"><button class="btn secondary" type="submit">${esc(t('changePassword'))}</button></div></form></section></div>
    ${admin?`<section class="panel"><div class="section-head"><h2>${esc(t('cloudBackup'))}</h2></div><p class="hint">${esc(t('cloudBackupHelp'))}</p></section>`:''}</div>`;
  if(admin) q('#settings-form').onsubmit=async e=>{e.preventDefault();try{await api('settings',formData(e.currentTarget));toast(t('saved'))}catch(err){handleError(err)}};
  q('#password-form').onsubmit=async e=>{e.preventDefault();try{await api('change-password',formData(e.currentTarget));toast(t('passwordChanged'));await start()}catch(err){handleError(err)}};
};
