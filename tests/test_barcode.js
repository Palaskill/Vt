'use strict';
const assert=require('node:assert/strict');
const barcode=require('../static/barcode.js');
const L=['0001101','0011001','0010011','0111101','0100011','0110001','0101111','0111011','0110111','0001011'];
const G=['0100111','0110011','0011011','0100001','0011101','0111001','0000101','0010001','0001001','0010111'];
const R=L.map(x=>[...x].map(b=>b==='1'?'0':'1').join(''));
const parity=['LLLLLL','LLGLGG','LLGGLG','LLGGGL','LGLLGG','LGGLLG','LGGGLL','LGLGLG','LGLGGL','LGGLGL'];
function encode13(s){let bits='101';const group=parity[Number(s[0])];for(let i=0;i<6;i++)bits+=(group[i]==='L'?L:G)[Number(s[i+1])];bits+='01010';for(let i=7;i<13;i++)bits+=R[Number(s[i])];return bits+'101'}
function encode8(s){let bits='101';for(let i=0;i<4;i++)bits+=L[Number(s[i])];bits+='01010';for(let i=4;i<8;i++)bits+=R[Number(s[i])];return bits+'101'}
function line(bits,size=4){const values=Array(50).fill(245);for(const bit of bits)for(let i=0;i<size;i++)values.push(bit==='1'?18:245);return Uint8Array.from(values.concat(Array(50).fill(245)))}
for(const [s,encode] of [['4006381333931',encode13],['96385074',encode8]]){
  assert(barcode.valid(s));assert.equal(barcode.decodeModules(encode(s)),s);
  assert.equal(barcode.decodeLine(line(encode(s))),s);
  assert.equal(barcode.decodeLine(Uint8Array.from([...line(encode(s))].reverse())),s);
}
assert(!barcode.valid('4006381333932'));
assert.equal(barcode.decodeModules(encode13('4006381333932')),null);
console.log('EAN-13 et EAN-8 : validation, lecture droite et inversée réussies.');
