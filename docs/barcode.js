/* Offline EAN-13/EAN-8 fallback. Native BarcodeDetector is preferred. */
(function(root){
  'use strict';
  const LEFT=['0001101','0011001','0010011','0111101','0100011','0110001','0101111','0111011','0110111','0001011'];
  const EVEN=['0100111','0110011','0011011','0100001','0011101','0111001','0000101','0010001','0001001','0010111'];
  const RIGHT=LEFT.map(s=>[...s].map(x=>x==='1'?'0':'1').join(''));
  const PARITY=['LLLLLL','LLGLGG','LLGGLG','LLGGGL','LGLLGG','LGGLLG','LGGGLL','LGLGLG','LGLGGL','LGGLGL'];
  function valid(s){if(!/^\d{8}$|^\d{13}$/.test(s))return false;let sum=0;for(let i=s.length-2,n=0;i>=0;i--,n++)sum+=Number(s[i])*(n%2===0?3:1);return (10-sum%10)%10===Number(s.at(-1))}
  function decodeModules(bits){
    if(bits.startsWith('101')&&bits.length>=95&&bits.slice(45,50)==='01010'&&bits.slice(92,95)==='101'){
      let digits='',parity='';
      for(let i=0;i<6;i++){const p=bits.slice(3+7*i,10+7*i);let n=LEFT.indexOf(p);if(n>=0){parity+='L';digits+=n}else{n=EVEN.indexOf(p);if(n<0)return null;parity+='G';digits+=n}}
      const first=PARITY.indexOf(parity);if(first<0)return null;digits=String(first)+digits;
      for(let i=0;i<6;i++){const n=RIGHT.indexOf(bits.slice(50+7*i,57+7*i));if(n<0)return null;digits+=n}
      if(valid(digits))return digits;
    }
    if(bits.startsWith('101')&&bits.length>=67&&bits.slice(31,36)==='01010'&&bits.slice(64,67)==='101'){
      let digits='';for(let i=0;i<4;i++){const n=LEFT.indexOf(bits.slice(3+7*i,10+7*i));if(n<0)return null;digits+=n}
      for(let i=0;i<4;i++){const n=RIGHT.indexOf(bits.slice(36+7*i,43+7*i));if(n<0)return null;digits+=n}
      if(valid(digits))return digits;
    }
    return null;
  }
  function decodeLine(values){
    let min=255,max=0;for(const n of values){if(n<min)min=n;if(n>max)max=n}if(max-min<55)return null;
    const threshold=(min+max)/2,original=Array.from(values,v=>v<threshold?1:0);
    for(const line of [original,original.slice().reverse()]){
      for(let i=2;i<line.length-70;i++){
        if(!line[i]||line[i-1])continue;
        let a=i;while(a<line.length&&line[a]===1)a++;
        let b=a;while(b<line.length&&line[b]===0)b++;
        let c=b;while(c<line.length&&line[c]===1)c++;
        const w1=a-i,w2=b-a,w3=c-b,unit=(w1+w2+w3)/3;
        if(unit<1||Math.max(w1,w2,w3)>unit*1.8||Math.min(w1,w2,w3)<unit*.4)continue;
        for(const scale of [1,0.97,1.03,0.94,1.06]){
          const m=unit*scale;let bits='';for(let j=0;j<95;j++){const at=Math.floor(i+(j+.5)*m);if(at>=line.length)break;bits+=line[at]?'1':'0'}
          const value=decodeModules(bits);if(value)return value;
        }
      }
    }
    return null;
  }
  function decodeImage(image,width,height){
    const rgba=image.data||image;
    for(const fraction of [.5,.4,.6,.3,.7,.2,.8]){
      const y=Math.min(height-1,Math.max(0,Math.floor(height*fraction))),line=new Uint8Array(width);
      for(let x=0;x<width;x++){const i=(y*width+x)*4;line[x]=Math.round(rgba[i]*.299+rgba[i+1]*.587+rgba[i+2]*.114)}
      const value=decodeLine(line);if(value)return value;
    }
    return null;
  }
  const api={valid,decodeModules,decodeLine,decodeImage};
  root.VetBarcode=api;
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
