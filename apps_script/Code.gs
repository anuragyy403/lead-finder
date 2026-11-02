/**
 * "Lead Finder" menu for the Google Sheet (Build Plan 6.5).
 *
 * Clicking "Find leads now" starts the GitHub Actions workflow "Find leads" (.github/workflows/run.yml),
 * which runs the Python script on GitHub's servers and writes the results back into this sheet.
 *
 * Install: Extensions -> Apps Script -> paste this file -> Save -> reload the sheet -> Lead Finder -> Set GitHub token.
 * See README "Sheet button".
 *
 * @OnlyCurrentDoc
 */

const REPO = 'anuragyy403/lead-finder';
const WORKFLOW = 'run.yml';
const MAX_REQUESTS_CAP = 60; // never let one click spend more than this many Google Places requests
const TOKEN_KEY = 'GITHUB_TOKEN';

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Lead Finder')
    .addItem('Find leads now', 'findLeads')
    .addItem('Test run (writes nothing)', 'testRun')
    .addItem('Check last run', 'checkLastRun')
    .addSeparator()
    .addItem('Set GitHub token', 'setToken')
    .addToUi();
}

function findLeads() {
  startRun_(false);
}

function testRun() {
  startRun_(true);
}

function startRun_(dryRun) {
  const ui = SpreadsheetApp.getUi();
  const token = getToken_();
  if (!token) return;

  const targets = activeTargets_();
  if (!targets.length) {
    ui.alert('No active targets', "Tick 'Active' on at least one row in the Targets tab first.", ui.ButtonSet.OK);
    return;
  }

  // Each target uses up to 3 requests (60 results); cap the whole run so a long list can't run up a bill
  const maxRequests = Math.min(targets.length * 3, MAX_REQUESTS_CAP);
  const list = targets.slice(0, 10).map(t => '• ' + t).join('\n') + (targets.length > 10 ? '\n…and more' : '');
  const answer = ui.alert(
    dryRun ? 'Start a test run?' : 'Find leads now?',
    'Searching ' + targets.length + ' target(s):\n' + list +
      '\n\nUp to ' + maxRequests + ' Google requests. Takes about 5–10 minutes.' +
      (dryRun ? '\nTest run: nothing will be written to the sheet.' : ''),
    ui.ButtonSet.OK_CANCEL);
  if (answer !== ui.Button.OK) return;

  const resp = github_('post', '/actions/workflows/' + WORKFLOW + '/dispatches', {
    ref: 'main',
    inputs: { dry_run: String(dryRun), reaudit: 'false', max_requests: String(maxRequests) },
  });
  if (resp.getResponseCode() === 204) {
    SpreadsheetApp.getActive().toast(
      'Started. New leads appear in Maps Leads in about 5–10 minutes. Use Lead Finder → Check last run.',
      'Lead Finder', 10);
  } else {
    ui.alert('Could not start the run', explain_(resp), ui.ButtonSet.OK);
  }
}

function checkLastRun() {
  const ui = SpreadsheetApp.getUi();
  if (!getToken_()) return;
  const resp = github_('get', '/actions/workflows/' + WORKFLOW + '/runs?per_page=1');
  if (resp.getResponseCode() !== 200) {
    ui.alert('Could not check', explain_(resp), ui.ButtonSet.OK);
    return;
  }
  const run = JSON.parse(resp.getContentText()).workflow_runs[0];
  if (!run) {
    ui.alert('No runs yet', 'Use Lead Finder → Find leads now.', ui.ButtonSet.OK);
    return;
  }
  const started = Utilities.formatDate(new Date(run.created_at), 'Asia/Kolkata', 'dd MMM, HH:mm');
  const state = run.status !== 'completed' ? '⏳ Still running (' + run.status + ')'
    : run.conclusion === 'success' ? '✅ Finished. Check the Maps Leads tab.'
    : '❌ Failed (' + run.conclusion + '). Open the link below to see why.';
  ui.alert('Last run: ' + started, state + '\n\n' + run.html_url, ui.ButtonSet.OK);
}

function setToken() {
  const ui = SpreadsheetApp.getUi();
  const answer = ui.prompt(
    'Set GitHub token',
    'Paste a fine-grained GitHub token for ' + REPO + ' with "Actions: Read and write" permission.',
    ui.ButtonSet.OK_CANCEL);
  if (answer.getSelectedButton() !== ui.Button.OK) return;
  const token = answer.getResponseText().trim();
  if (!token) return;
  PropertiesService.getScriptProperties().setProperty(TOKEN_KEY, token);

  const resp = github_('get', '/actions/workflows/' + WORKFLOW);
  ui.alert(resp.getResponseCode() === 200 ? '✅ Token saved and working.' : '⚠️ Token saved, but GitHub refused it.\n\n' +
    explain_(resp), ui.ButtonSet.OK);
}

function getToken_() {
  const token = PropertiesService.getScriptProperties().getProperty(TOKEN_KEY);
  if (!token) {
    SpreadsheetApp.getUi().alert('GitHub token missing', 'Use Lead Finder → Set GitHub token first.',
      SpreadsheetApp.getUi().ButtonSet.OK);
  }
  return token;
}

function activeTargets_() {
  const sheet = SpreadsheetApp.getActive().getSheetByName('Targets');
  if (!sheet) return [];
  const rows = sheet.getDataRange().getValues();
  const header = rows.shift();
  const active = header.indexOf('Active'), category = header.indexOf('Category'), city = header.indexOf('City');
  return rows
    .filter(r => r[active] === true && String(r[category]).trim() && String(r[city]).trim())
    .map(r => String(r[category]).trim() + ' in ' + String(r[city]).trim());
}

function github_(method, path, body) {
  const options = {
    method: method,
    muteHttpExceptions: true,
    headers: {
      Authorization: 'Bearer ' + PropertiesService.getScriptProperties().getProperty(TOKEN_KEY),
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
    },
  };
  if (body) {
    options.contentType = 'application/json';
    options.payload = JSON.stringify(body);
  }
  return UrlFetchApp.fetch('https://api.github.com/repos/' + REPO + path, options);
}

function explain_(resp) {
  const code = resp.getResponseCode();
  if (code === 401) return 'GitHub says the token is wrong or expired (401). Use Lead Finder → Set GitHub token.';
  if (code === 403 || code === 404) {
    return 'GitHub refused (' + code + '). The token needs access to ' + REPO +
      ' with "Actions: Read and write".';
  }
  return 'GitHub answered ' + code + ': ' + resp.getContentText().slice(0, 300);
}
