import {createRequire} from 'node:module';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const require = createRequire(path.resolve('frontend/package.json'));
const {chromium} = require('playwright');
const caseRoot = path.dirname(fileURLToPath(import.meta.url));
const auth = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
const browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  headless:true,args:['--disable-gpu','--renderer-process-limit=2']});
const context = await browser.newContext({viewport:{width:1280,height:900}});
await context.addCookies([{name:'forgex_local_session',value:auth.token,url:'http://127.0.0.1:3300',httpOnly:true,sameSite:'Strict'}]);
const page = await context.newPage();
const network=[];
page.on('response',response => {
  if (response.url().includes('/api/forgex/')) network.push({path:new URL(response.url()).pathname,
    method:response.request().method(),status:response.status()});
});
page.on('pageerror',error => console.log(JSON.stringify({pageError:error.message})));
await page.goto('http://127.0.0.1:3300',{waitUntil:'domcontentloaded',timeout:120000});
console.log('BROWSER_READY');
let sequence=0;
while (true) {
  try {
    const command=JSON.parse(await fs.readFile(path.join(caseRoot,'ui-command.json'),'utf8'));
    if (command.sequence>sequence) {
      sequence=command.sequence;
      let result;
      try {
        if (command.op==='click') await page.getByRole(command.role??'button',{name:command.name,exact:true}).filter({visible:true}).click({timeout:20000});
        if (command.op==='check') await page.getByRole('checkbox',{name:command.name,exact:true}).check({timeout:20000});
        if (command.op==='project') await page.getByText('ESP32_Smart_Monitor',{exact:true}).filter({visible:true}).click({timeout:20000});
        if (command.op==='prompt') await page.getByRole('textbox',{name:'Message',exact:true}).filter({visible:true})
          .fill(await fs.readFile(path.join(caseRoot,'prompt.txt'),'utf8'),{timeout:20000});
        if (command.op==='profile') await page.getByRole('combobox',{name:'Task allowance'}).selectOption('advanced');
        if (command.op==='select') await page.getByRole('combobox',{name:command.name}).selectOption(command.value);
        if (command.op==='finish') break;
        const text=await page.locator('body').innerText({timeout:20000});
        const controls=await page.locator('input,select,textarea,button').evaluateAll(es=>es.filter(e=>e.getBoundingClientRect().width&&e.getBoundingClientRect().height)
          .map(e=>({tag:e.tagName,name:e.getAttribute('aria-label')??e.textContent,value:e.tagName==='TEXTAREA'?e.value.length:e.value,disabled:e.disabled})));
        await page.screenshot({path:path.join(caseRoot,'ui-current.png')});
        result={sequence,op:command.op,text,controls,network:network.slice(-60)};
      } catch(error) { result={sequence,op:command.op,error:error.message}; }
      await fs.writeFile(path.join(caseRoot,'ui-state.json'),JSON.stringify(result,null,2));
      console.log(JSON.stringify({sequence,op:command.op,error:result.error,text:result.text?.slice(-3000)}));
    }
  } catch(error) { if (error.code!=='ENOENT') console.log(JSON.stringify({controlError:error.message})); }
  await new Promise(resolve=>setTimeout(resolve,500));
}
await browser.close();
