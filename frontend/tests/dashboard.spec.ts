import { test, expect } from '@playwright/test';
const token = 'test-token-'.repeat(4);
const fixture:Record<string,unknown> = {
 system:{cpu_percent:12,memory:{total:64000000000,used:16000000000,percent:25},disk:{percent:32},gpus:{available:false,devices:[],message:'No GPU installed'},sampled_at:'2026-11-15T00:00:00Z'},
 engines:{items:['kmic','pxa','qr0n'].map(id=>({id,unit:'p100-lab-'+id+'.service',state:'inactive',note:'Service state only'})),controls_enabled:false},
 models:{items:[{root:1,name:'test-model.gguf',bytes:1073741824}],warnings:[],truncated:false},
 benchmarks:{items:[{id:1,engine:'pxa',model:'test-model.gguf',tokens_per_second:12.5,created_at:'2026-11-15T00:00:00Z',source:'manual'}]}
};
test('authentication, monitoring, disabled controls and token clearing',async({page})=>{
 await page.route('**/api/**',async route=>{
   expect(route.request().headers()['authorization']).toBe('Bearer '+token);
   await route.fulfill({json:fixture[route.request().url().split('/').pop()!]});
 });
 await page.goto('/');
 await expect(page.getByRole('heading',{name:'Connect to your lab'})).toBeVisible();
 await page.getByLabel('Access token').fill(token);
 await page.getByRole('button',{name:'Connect securely'}).click();
 await expect(page.getByText('LIVE HOST DATA',{exact:true})).toBeVisible();
 await expect(page.getByText('No GPUs reported.',{exact:false})).toBeVisible();
 await expect(page.getByRole('button',{name:'start',exact:true}).first()).toBeDisabled();
 await expect(page.getByRole('cell',{name:'test-model.gguf',exact:true})).toBeVisible();
 expect(await page.evaluate(()=>Object.keys(localStorage).length+Object.keys(sessionStorage).length)).toBe(0);
 await page.screenshot({path:'test-results/dashboard-desktop.png',fullPage:true});
 await page.getByRole('button',{name:'Disconnect & clear token'}).click();
 await expect(page.getByLabel('Access token')).toHaveValue('');
});
test('authentication failure is visible and mobile layout fits',async({page})=>{
 await page.setViewportSize({width:390,height:844});
 await page.route('**/api/**',route=>route.fulfill({status:401,json:{detail:'Unauthorized'}}));
 await page.goto('/');
 await page.getByLabel('Access token').fill(token);
 await page.getByRole('button',{name:'Connect securely'}).click();
 await expect(page.getByRole('alert')).toContainText('Authentication failed');
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
});
