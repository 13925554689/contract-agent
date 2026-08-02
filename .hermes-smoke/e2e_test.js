// 合同生成审核智能体 - 浏览器E2E + 前后端功能匹配验证
const puppeteer = require('puppeteer-core');
const path = require('path');

const BASE = 'http://127.0.0.1:5198';
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const TEST_FILE = path.resolve(__dirname, '..', 'test_contract.txt');

const findings = [];
function log(tag, msg) { console.log(`[${tag}] ${msg}`); }
function finding(sev, cat, title, detail) {
  findings.push({ sev, cat, title, detail });
  log(sev, `${title} — ${detail}`);
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu']
  });
  const page = await browser.newPage();
  const consoleErrors = [];
  const pageErrors = [];
  const apiCalls = [];

  page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()); });
  page.on('pageerror', e => pageErrors.push(String(e)));
  page.on('request', r => { if (r.url().includes('/api/')) apiCalls.push(r.method() + ' ' + new URL(r.url()).pathname); });

  // 1. 打开首页
  await page.goto(BASE, { waitUntil: 'networkidle2', timeout: 30000 });
  const title = await page.title();
  log('PAGE', `标题="${title}"`);
  if (!title.includes('合同')) finding('HIGH', 'content', '页面标题异常', `title="${title}"`);

  // health状态显示
  await new Promise(r => setTimeout(r, 800));
  const statusText = await page.$eval('#status', el => el.textContent);
  log('STATUS', statusText.trim());
  if (!statusText.includes('法规库: 8条')) finding('MEDIUM', 'functional', '健康状态未渲染', statusText.trim());

  // 2. Tab切换
  const tabCount = await page.$$eval('.tab', els => els.length);
  log('TABS', `共${tabCount}个tab`);
  await page.evaluate(() => switchTab('generate'));
  await new Promise(r => setTimeout(r, 200));
  const genVisible = await page.$eval('#panel-generate', el => el.classList.contains('active'));
  if (!genVisible) finding('HIGH', 'functional', 'Tab切换到生成页失败', 'panel-generate未激活');
  await page.evaluate(() => switchTab('regulations'));
  await new Promise(r => setTimeout(r, 200));
  const regVisible = await page.$eval('#panel-regulations', el => el.classList.contains('active'));
  if (!regVisible) finding('HIGH', 'functional', 'Tab切换到法规库失败', 'panel-regulations未激活');
  await page.evaluate(() => switchTab('review'));
  await new Promise(r => setTimeout(r, 200));

  // 3. 审核流程（真实文件上传）
  const fileInput = await page.$('#fileInput');
  await fileInput.uploadFile(TEST_FILE);
  await new Promise(r => setTimeout(r, 300));
  const fileNameText = await page.$eval('#fileName', el => el.textContent);
  log('UPLOAD', fileNameText.trim());
  await page.click('text=开始审核');
  await page.waitForSelector('#reviewResult.show', { timeout: 30000 }).catch(() => {});
  await new Promise(r => setTimeout(r, 500));
  const riskVisible = await page.$eval('#reviewResult', el => el.classList.contains('show'));
  if (!riskVisible) finding('CRITICAL', 'functional', '审核结果未显示', 'reviewResult未show');
  else {
    const overall = await page.$eval('#overallRisk', el => el.textContent);
    const riskItems = await page.$$eval('.risk-item', els => els.length);
    const sugs = await page.$$eval('#suggestions li', els => els.length);
    log('REVIEW', `综合风险="${overall}" 风险项=${riskItems} 建议=${sugs}`);
    if (riskItems === 0) finding('MEDIUM', 'functional', '审核无风险项', '规则引擎未命中任何风险');
    const msg = await page.$eval('#reviewMsg', el => el.textContent);
    if (!msg.includes('审核完成')) finding('MEDIUM', 'functional', '审核成功提示缺失', msg);
  }

  // 4. 导出报告（客户端blob）
  await page.evaluate(() => exportReport());
  await new Promise(r => setTimeout(r, 300));
  log('EXPORT-REPORT', '导出报告按钮已触发(客户端blob)');

  // 5. 生成流程（模板降级）
  await page.evaluate(() => switchTab('generate'));
  await page.type('#genReq', '采购100台服务器，3年维保，预付30%+验收60%+质保10%，交货30天');
  await page.type('#genBuyer', '甲方科技有限公司');
  await page.type('#genSeller', '乙方信息技术公司');
  await page.click('text=生成合同');
  await page.waitForSelector('#genResult.show', { timeout: 30000 }).catch(() => {});
  await new Promise(r => setTimeout(r, 500));
  const genShown = await page.$eval('#genResult', el => el.classList.contains('show'));
  if (!genShown) finding('CRITICAL', 'functional', '生成结果未显示', 'genResult未show');
  else {
    const genText = await page.$eval('#genText', el => el.textContent.length);
    const genMsg = await page.$eval('#genMsg', el => el.textContent);
    log('GENERATE', `生成文本${genText}字 | ${genMsg.trim()}`);
    if (genText < 100) finding('HIGH', 'functional', '生成文本过短', `${genText}字`);
  }

  // 6. 生成+下载DOCX —— CDP下载行为验证文件真实落盘
  const fs = require('fs');
  const DL_DIR = path.join(__dirname, 'dl');
  fs.rmSync(DL_DIR, { recursive: true, force: true });
  fs.mkdirSync(DL_DIR, { recursive: true });
  const client = await page.createCDPSession();
  await client.send('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: DL_DIR, eventsEnabled: true });

  apiCalls.length = 0;
  await page.evaluate(() => generateContract(true));
  await page.waitForSelector('#genResult.show', { timeout: 30000 }).catch(() => {});
  await new Promise(r => setTimeout(r, 2500));
  const genMsg2 = await page.$eval('#genMsg', el => el.textContent);
  log('GEN-DOCX', genMsg2.trim());
  const dlFiles = fs.existsSync(DL_DIR) ? fs.readdirSync(DL_DIR) : [];
  if (dlFiles.length === 0) {
    finding('HIGH', 'mismatch', '前端"生成并下载DOCX"未产生下载文件', genMsg2.trim());
  } else {
    const buf = fs.readFileSync(path.join(DL_DIR, dlFiles[0]));
    const valid = buf.length > 1000 && buf.slice(0, 2).toString('latin1') === 'PK';
    log('DOWNLOAD', `下载落盘: ${dlFiles[0]} (${buf.length}B, PK=${valid})`);
    if (!valid) finding('HIGH', 'functional', '下载文件非有效DOCX', `${dlFiles[0]} ${buf.length}B`);
  }

  // 6b. 闭环生成+自动审核
  apiCalls.length = 0;
  await page.evaluate(() => switchTab('generate'));
  await page.evaluate(() => generateAndReview());
  await page.waitForFunction(() => document.getElementById('genMsg').textContent.includes('闭环'), { timeout: 30000 }).catch(() => {});
  await new Promise(r => setTimeout(r, 500));
  const loopMsg = await page.$eval('#genMsg', el => el.textContent);
  log('GEN-LOOP', loopMsg.trim());
  const historyItems = await page.$$eval('#genHistory > div', els => els.length);
  log('GEN-LOOP-HISTORY', `历史条目${historyItems}个`);
  if (!loopMsg.includes('闭环')) finding('HIGH', 'functional', '闭环按钮失败', loopMsg);

  // 7. 法规库搜索
  await page.evaluate(() => switchTab('regulations'));
  await page.type('#regSearch', '违约金');
  await page.click('text=搜索');
  await page.waitForFunction(() => document.querySelectorAll('#regResults .reg-card').length > 0, { timeout: 15000 }).catch(() => {});
  await new Promise(r => setTimeout(r, 300));
  const regCards = await page.$$eval('#regResults .reg-card', els => els.length);
  const regTitle = regCards ? await page.$eval('#regResults .reg-card .rtitle', el => el.textContent) : '';
  log('REG-SEARCH', `结果${regCards}条 首条="${regTitle}"`);
  if (regCards === 0) finding('HIGH', 'functional', '法规搜索无结果', 'q=违约金 返回0条');

  // 8. 法规刷新
  await page.click('text=刷新');
  await new Promise(r => setTimeout(r, 1000));
  const regMsg = await page.$eval('#regMsg', el => el.textContent);
  log('REG-REFRESH', regMsg.trim());
  if (!regMsg.includes('已刷新')) finding('MEDIUM', 'functional', '法规刷新提示缺失', regMsg);

  // 9. 收集最终结果
  const finalApiCalls = [...new Set(apiCalls)];
  log('API-CALLS', JSON.stringify(finalApiCalls));

  await page.screenshot({ path: path.join(__dirname, 'e2e_final.png'), fullPage: true });

  console.log('\n===== JS错误汇总 =====');
  console.log('console.error:', consoleErrors.length ? consoleErrors : '无');
  console.log('pageerror:', pageErrors.length ? pageErrors : '无');

  console.log('\n===== 发现汇总 =====');
  findings.forEach((f, i) => console.log(`[${f.sev}] ${f.cat}: ${f.title}`));

  await browser.close();
  process.exit(consoleErrors.length + pageErrors.length > 0 ? 1 : 0);
})().catch(e => { console.error('HARNESS FAIL', e); process.exit(2); });
