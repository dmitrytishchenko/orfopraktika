"""Браузерные тесты ОрфоПрактики.

Запуск:
    pip install playwright
    python -m playwright install chromium
    python tests/test_app.py            # скриншоты сохраняются в tests/screenshots/

Проверяют банк заданий, все режимы занятий, хранение и миграцию данных,
профили, импорт, две вкладки и офлайн-режим (через локальный сервер).
"""
import datetime, functools, http.server, json, pathlib, sys, threading
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILE_URL = (ROOT / 'index.html').as_uri()
SHOTS = ROOT / 'tests' / 'screenshots'
SHOTS.mkdir(exist_ok=True)
KEY = 'orfopractice.v1'
fails = []


def check(cond, msg):
    print(('PASS ' if cond else 'FAIL ') + msg)
    if not cond:
        fails.append(msg)


def iso(days_ago=0, minutes=0):
    t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago, minutes=minutes)
    return t.strftime('%Y-%m-%dT%H:%M:%S.000Z')


def serve():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    handler = functools.partial(Quiet, directory=str(ROOT))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


with sync_playwright() as p:
    browser = p.chromium.launch()

    def fresh(seed=None, viewport=None, url=FILE_URL):
        ctx = browser.new_context(viewport=viewport or {'width': 1200, 'height': 900}, accept_downloads=True)
        ctx.set_default_timeout(10000)
        pg = ctx.new_page()
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        pg.on('console', lambda m: errs.append(m.text) if m.type == 'error' else None)
        pg.goto(url)
        pg.evaluate('()=>localStorage.clear()')
        if seed:
            pg.evaluate('v=>{for(const [k,x] of Object.entries(v))localStorage.setItem(k,x)}', seed)
        pg.reload()
        return ctx, pg, errs

    store = lambda pg, key=KEY: pg.evaluate('k=>JSON.parse(localStorage.getItem(k))', key)
    cur = lambda pg, expr: pg.evaluate(f'()=>{{const q=QM[db.active.ids[db.active.index]];return {expr}}}')

    def answer(pg, text):
        pg.fill('#answer', text)
        pg.click('#answerForm button[type=submit]')

    def solve(pg, right=True):
        """Отвечает на текущее задание любого типа."""
        kind = pg.evaluate('()=>db.active.kind')
        if kind == 'finderror':
            target = pg.evaluate('()=>{const r=db.active.rounds[db.active.index];return right?r.wrong:r.ids.find(i=>i!==r.wrong)}'.replace('right', 'true' if right else 'false'))
            pg.click(f'[data-pick="{target}"]')
        elif kind == 'dictation':
            answer(pg, cur(pg, 'q.full') if right else 'щщщ')
        elif pg.locator('[data-opt]').count():
            opt = cur(pg, 'q.answer') if right else cur(pg, 'TM[q.topic].choice.find(o=>o!==q.answer)')
            pg.click(f'[data-opt="{opt}"]')
        else:
            a = cur(pg, 'q.answer') if right else 'щ'
            if a == '':
                pg.click('#empty'); pg.click('#answerForm button[type=submit]')
            else:
                answer(pg, a)
        if pg.locator('[data-why]').count():
            pg.click(f'[data-why="{cur(pg, "q.reason")}"]')

    def finish_session(pg, right=True):
        while pg.evaluate('()=>!!db.active'):
            if not pg.evaluate('()=>!!currentAttempt()'):
                solve(pg, right)
            pg.click('#next')

    # ---------- Банк заданий ----------
    ctx, pg, errs = fresh()
    bank = pg.evaluate('''()=>{
      const problems=[];
      for(const t of TOPICS)for(const it of t.items){
        const [n,text,exp]=it,id=t.id+'_'+n;
        if((text.match(/\\[[^\\]]*\\]/g)||[]).length!==1)problems.push(id+': не один пропуск');
        if(/[a-z]/i.test((text+exp).replace(/\\bI{1,2}\\b/g,'')))problems.push(id+': латиница');
        if(!t.choice&&text.match(/\\[([^\\]]*)\\]/)[1].length>4)problems.push(id+': длинный ответ');
        if(!exp)problems.push(id+': нет пояснения');
      }
      for(const q of QUESTIONS){const t=TM[q.topic];if(t.reasons&&(q.reason===null||q.reason>=t.reasons.length))problems.push(q.id+': нет правила для «Почему?»')}
      return {problems,topics:TOPICS.length,items:QUESTIONS.length,unique:new Set(QUESTIONS.map(q=>q.id)).size,
              texts:QUESTIONS.length-new Set(QUESTIONS.map(q=>q.topic+q.text)).size,groups:TOPICS.every(t=>GROUPS.includes(t.group))}}''')
    check(bank['problems'] == [], f'bank is consistent {bank["problems"]}')
    check(bank['items'] == bank['unique'] and bank['texts'] == 0 and bank['groups'], f'{bank["topics"]} topics, {bank["items"]} unique items')
    check(f'{bank["items"]} заданий' in pg.inner_text('#app') and pg.locator('h3.group').count() == 4, 'home shows bank size and groups')
    check(pg.evaluate('()=>QM.constructor===undefined && !isKnown("toString")'), 'no prototype keys in lookups')

    # ---------- Диагностика ----------
    pg.click('#diagnostic')
    n = pg.evaluate('()=>db.active.ids.length')
    check(n == bank['topics'] * 2 and pg.locator('#hint').count() == 0, f'diagnostic: {n} items, no hints')
    while pg.evaluate('()=>!!db.active'):
        bad = cur(pg, 'q.topic') in ('vowel', 'prefix')
        solve(pg, right=not bad)
        check(pg.locator('[data-why]').count() == 0, 'no «why» in diagnostic') if not bad and cur(pg, 'q.reason') is not None else None
        pg.click('#next')
    check(pg.evaluate('()=>db.attempts.length') == n, 'diagnostic adds no retries')
    txt = pg.inner_text('#app')
    check('Результат по темам' in txt and 'Ошибки есть в 2 темах' in txt, 'diagnostic result per topic')
    pg.screenshot(path=str(SHOTS / 'diagnostic-result.png'), full_page=True)
    pg.click('#focus')
    act = pg.evaluate('()=>({kind:db.active.kind,topics:[...new Set(db.active.ids.map(i=>QM[i].topic))].sort()})')
    check(act == {'kind': 'focus', 'topics': ['prefix', 'vowel']}, f'focus session on weak topics {act}')
    check(not errs, f'diagnostic: no console errors {errs}')
    ctx.close()

    # ---------- Ввод, повтор ошибки, «Почему?» ----------
    ctx, pg, errs = fresh()
    pg.evaluate('()=>{start("topic","t",["prefix_1","prefix_0"])}'); pg.wait_for_selector('#answer')
    if cur(pg, 'q.id') != 'prefix_1':
        solve(pg); pg.click('#next')
    answer(pg, 'c')
    check('английская раскладка' in pg.inner_text('#inputError'), 'latin layout detected')
    answer(pg, 'расписать')
    check('не всё слово' in pg.inner_text('#inputError'), 'whole word detected')
    check(pg.evaluate('()=>db.attempts.length') == 0, 'bad input not saved')
    answer(pg, 'с')
    check(pg.locator('[data-why]').count() == 3 and pg.locator('#next').count() == 0, '«why» asked after correct answer, next hidden')
    pg.screenshot(path=str(SHOTS / 'why.png'), full_page=True)
    pg.click('[data-why="0"]')
    check(pg.evaluate('()=>db.attempts.at(-1).why') is False and 'другое правило' in pg.inner_text('#app'), 'wrong reason recorded')
    pg.click('#next')
    if not pg.evaluate('()=>!!db.active'):
        pg.evaluate('()=>{start("topic","t",["prefix_0"])}'); pg.wait_for_selector('#answer')
    answer(pg, 'щ')
    check(pg.evaluate('()=>db.active.ids.length') == pg.evaluate('()=>db.active.base') + 1, 'wrong answer queued for retry')
    pg.click('#next')
    check('Повтор ошибки' in pg.inner_text('#app'), 'retry step marked')
    solve(pg); pg.click('#next')
    check('исправлено 1 из 1' in pg.inner_text('#app'), 'result shows fixed retry')
    pg.evaluate('()=>{start("topic","t",["tsya_0"])}'); pg.wait_for_selector('#answer')
    answer(pg, '-тся')
    check(pg.evaluate('()=>db.attempts.at(-1).correct') is True, 'leading dash accepted')
    pg.evaluate('()=>{db.settings.why=false;save()}')
    pg.evaluate('()=>{start("topic","t",["oe_0"])}')
    pg.click('dialog button[value=ok]'); pg.wait_for_selector('#answer')
    pg.click('[data-key="ё"]')
    check(pg.input_value('#answer') == 'ё', 'ё key inserts the letter')
    answer(pg, 'е')
    check('именно буква ё' in pg.inner_text('#app'), 'ё near-miss hint')
    pg.evaluate('()=>{start("topic","t",["oe_1"])}')
    pg.click('dialog button[value=ok]'); pg.wait_for_selector('#answer')
    answer(pg, 'ё')
    check(pg.locator('[data-why]').count() == 0, '«why» can be switched off')
    check(not errs, f'input: no console errors {errs}')
    ctx.close()

    # ---------- НЕ с глаголами, мини-диктант, «Найди ошибку» ----------
    ctx, pg, errs = fresh(viewport={'width': 390, 'height': 844})
    pg.evaluate('()=>{start("topic","НЕ",["ne_1","ne_0"])}'); pg.wait_for_selector('[data-opt]')
    first = cur(pg, 'q.id')
    pg.screenshot(path=str(SHOTS / 'choice-mobile.png'), full_page=True)
    pg.click('[data-opt="слитно"]')
    check(pg.evaluate('()=>db.attempts.at(-1).correct') == (first == 'ne_1'), 'choice answer checked')
    ctx.close()

    ctx, pg, errs = fresh(viewport={'width': 390, 'height': 844})
    pg.click('#dictation')
    check(pg.evaluate('()=>db.active.kind') == 'dictation', 'dictation started')
    ids = pg.evaluate('()=>db.active.ids')
    check(all(pg.evaluate('id=>QM[id].single&&!DICTATION_SKIP.has(id)', i) for i in ids), 'dictation uses single words only')
    check('Мини-диктант' in pg.inner_text('#app') and pg.locator('.word').count() == 0, 'dictation hides the word')
    pg.screenshot(path=str(SHOTS / 'dictation-mobile.png'), full_page=True)
    pg.evaluate('()=>{db.active.ids[db.active.index]="oe_0";save()}'); pg.evaluate('()=>renderQuiz()')
    answer(pg, 'Шепот')
    check(pg.evaluate('()=>db.attempts.at(-1).correct') is True, 'dictation accepts е for ё and capitals')
    pg.click('#next')
    answer(pg, 'щщщ')
    check(pg.evaluate('()=>db.attempts.at(-1).correct') is False and 'Правильно:' in pg.inner_text('#app'), 'dictation wrong word shows the right one')
    ctx.close()

    ctx, pg, errs = fresh(viewport={'width': 390, 'height': 844})
    pg.click('#find')
    r = pg.evaluate('()=>db.active.rounds[0]')
    check(len(r['ids']) == 4 and r['wrong'] in r['ids'] and r['shown'] != pg.evaluate('id=>QM[id].full', r['wrong']), 'round has 4 words with one misspelled')
    pg.screenshot(path=str(SHOTS / 'find-error-mobile.png'), full_page=True)
    solve(pg, right=True)
    check(pg.evaluate('()=>db.attempts.at(-1).correct') is True and pg.locator('.picks .is-wrong').count() == 1, 'found the error')
    pg.click('#next')
    base = pg.evaluate('()=>db.active.ids.length')
    solve(pg, right=False)
    check(pg.evaluate('()=>db.active.ids.length') == base + 1 and pg.evaluate('()=>db.active.rounds.length') == base + 1, 'missed error queued with a new round')
    pg.screenshot(path=str(SHOTS / 'find-error-wrong-mobile.png'), full_page=True)
    pg.reload()
    check(pg.evaluate('()=>db.active?.rounds?.length') == base + 1, 'rounds survive reload')
    pg.click('#resume')
    finish_session(pg)
    check(not errs, f'formats: no console errors {errs}')
    ctx.close()

    # ---------- Цель на день и дни подряд ----------
    hist = {'version': 2, 'attempts': [], 'sessions': [], 'active': None, 'settings': {'why': True, 'goal': 5}}
    step = 0
    for day in (2, 1):
        for i in range(5):
            hist['attempts'].append({'sid': f's{day}', 'step': i, 'qid': f'vowel_{i}', 'input': 'а', 'correct': True, 'hinted': False, 'skipped': False, 'at': iso(day, -i)})
    ctx, pg, errs = fresh({KEY: json.dumps(hist)})
    check(pg.evaluate('()=>streak()') == 2 and 'Цель на сегодня: 0 из 5' in pg.inner_text('#app'), 'streak counts previous days')
    pg.click('#mixed')
    for _ in range(5):
        solve(pg); pg.click('#next')
    pg.click('#pause')
    check(pg.evaluate('()=>streak()') == 3 and 'Цель на сегодня выполнена' in pg.inner_text('#app'), 'goal reached today extends streak')
    pg.screenshot(path=str(SHOTS / 'home.png'))
    with pg.expect_download() as dl:
        pg.click('nav [data-page=stats]'); pg.click('#csvErrors')
    check(dl.value.suggested_filename.endswith('ошибки.csv'), 'errors CSV downloads')
    pg.screenshot(path=str(SHOTS / 'stats.png'), full_page=True)
    check(not errs, f'goal: no console errors {errs}')
    ctx.close()

    # ---------- Ученики ----------
    ctx, pg, errs = fresh({KEY: json.dumps(hist)})
    pg.click('nav [data-page=data]')
    pg.fill('#newName', 'Маша'); pg.click('#addProfile button')
    check(pg.inner_text('#who') == 'Ученик: Маша' and pg.evaluate('()=>db.attempts.length') == 0, 'new student starts empty')
    pg.click('#mixed'); solve(pg); pg.click('#next')
    masha_key = pg.evaluate('()=>KEY')
    pg.click('nav [data-page=data]')
    pg.screenshot(path=str(SHOTS / 'settings.png'), full_page=True)
    pg.click('[data-switch="default"]')
    check(pg.evaluate('()=>db.attempts.length') == 10, 'first student history intact')
    check(len(store(pg, masha_key)['attempts']) == 1, 'second student stored separately')
    pg.reload()
    check(pg.evaluate('()=>profiles.current') == 'default', 'current student remembered')
    pg.click('nav [data-page=data]')
    pg.click('[data-remove]:not([data-remove="default"])'); pg.click('dialog button[value=ok]')
    pg.wait_for_function('()=>profiles.list.length===1')
    check(pg.evaluate('k=>localStorage.getItem(k)', masha_key) is None and pg.locator('#who.hidden').count() == 1, 'student removed with data')
    check(not errs, f'profiles: no console errors {errs}')
    ctx.close()

    # ---------- Миграция v1, повреждённые данные, неизвестные задания ----------
    v1 = {'version': 1, 'attempts': [
        {'sid': 's1', 'step': 0, 'qid': 'vowel_0', 'input': 'е', 'correct': True, 'hinted': False, 'skipped': False, 'at': iso(3)},
        {'sid': 's2', 'step': 0, 'qid': 'prefix_7', 'input': 'з', 'correct': False, 'hinted': False, 'skipped': False, 'at': iso(0, 30)},
        {'sid': 's2', 'step': 1, 'qid': 'constructor', 'input': 'е', 'correct': False, 'hinted': False, 'skipped': False, 'at': iso(0, 29)}],
        'sessions': [{'id': 's1', 'mode': 'Диагностика', 'at': iso(3), 'complete': False}],
        'active': {'id': 's2', 'mode': 'Повторение ошибок', 'ids': ['prefix_7', 'vowel_1'], 'index': 0, 'at': iso(0, 31), 'hint': False}}
    ctx, pg, errs = fresh({KEY: json.dumps(v1)})
    s = store(pg)
    check(s['version'] == 2 and len(s['attempts']) == 2 and len(s['orphans']) == 1, 'v1 migrated, unknown ids kept aside')
    check(s['active']['kind'] == 'review' and s['active']['base'] == 2 and s['settings']['why'] is True, 'kinds, base and settings derived')
    check(pg.evaluate('k=>localStorage.getItem(k+".v1-backup")!==null', KEY), 'v1 backup stored')
    check(set(pg.evaluate('()=>dueIds()')) == {'vowel_0', 'prefix_7'}, 'due list from history')
    check(not errs, f'migration: no console errors {errs}')
    ctx.close()

    ctx, pg, errs = fresh({KEY: '{broken json'})
    keys = pg.evaluate('()=>backupKeys()')
    check(pg.locator('#recoveryNotice:not(.hidden)').count() == 1 and len(keys) == 1, 'broken data stashed and reported')
    pg.click('#mixed'); solve(pg)
    check(store(pg)['version'] == 2 and pg.evaluate('k=>localStorage.getItem(k)', keys[0]) == '{broken json', 'stash survives saves')
    ctx.close()

    # ---------- Импорт с объединением ----------
    ctx, pg, errs = fresh({KEY: json.dumps(v1)})
    imp = {'version': 2, 'attempts': [
        {'sid': 'x1', 'step': 0, 'qid': 'soft_0', 'input': 'ь', 'correct': True, 'hinted': False, 'skipped': False, 'at': iso(2)},
        {'sid': 's1', 'step': 0, 'qid': 'vowel_0', 'input': 'е', 'correct': True, 'hinted': False, 'skipped': False, 'at': iso(3)}],
        'sessions': [{'id': 'x1', 'kind': 'topic', 'mode': 'Ь', 'at': iso(2), 'complete': True}], 'active': None}
    path = SHOTS / 'import.json'; path.write_text(json.dumps(imp))
    pg.click('nav [data-page=data]'); pg.set_input_files('#file', str(path))
    pg.click('dialog button[value=merge]')
    pg.wait_for_function('()=>document.getElementById("importStatus")?.textContent.includes("объединена")')
    check(len(store(pg)['attempts']) == 3, 'merge deduplicates attempts')
    path.unlink()
    ctx.close()

    # ---------- Две вкладки ----------
    ctx = browser.new_context()
    p1 = ctx.new_page(); p1.goto(FILE_URL); p1.evaluate('()=>localStorage.clear()'); p1.reload()
    p2 = ctx.new_page(); p2.goto(FILE_URL)
    p1.click('#mixed'); solve(p1)
    p2.wait_for_function('()=>db.attempts.length===1', timeout=3000)
    check(p2.locator('#toast:not(.hidden)').count() == 1, 'second tab picks up changes')
    ctx.close()

    # ---------- Внутри Android-приложения (мост OrfoAndroid) ----------
    ctx = browser.new_context(viewport={'width': 390, 'height': 844})
    ctx.add_init_script('''window.__calls=[];window.OrfoAndroid={
      ttsStatus:()=>"ready",
      speak:(t,r)=>__calls.push(["speak",t,r]),
      saveFile:(n,m,c)=>__calls.push(["save",n,m,c.length])};''')
    pg = ctx.new_page(); errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.goto(FILE_URL); pg.evaluate('()=>localStorage.clear()'); pg.reload()
    pg.click('#dictation')
    pg.wait_for_function('()=>__calls.some(c=>c[0]==="speak")')
    word = cur(pg, 'q.full')
    check(pg.evaluate('()=>__calls.find(c=>c[0]==="speak")[1]') == word, 'dictation speaks through Android voice')
    pg.click('#slow')
    check(pg.evaluate('()=>__calls.at(-1)[2]') == 0.6, 'slow button lowers the rate')
    check(pg.evaluate('()=>window.orfoBack()') is True and pg.evaluate('()=>page') == 'home', 'back from lesson goes home')
    check(pg.evaluate('()=>window.orfoBack()') is False, 'back on home lets the app close')
    pg.click('nav [data-page=data]')
    check('установлен как приложение для Android' in pg.inner_text('#app'), 'settings know they run in the app')
    pg.click('#backup')
    save = pg.evaluate('()=>__calls.find(c=>c[0]==="save")')
    check(save and save[1].endswith('.json') and save[2] == 'application/json' and save[3] > 10, f'backup saved through Android {save}')
    pg.click('#csv')
    check(pg.evaluate('()=>__calls.at(-1)[2]') == 'text/csv', 'CSV saved without charset parameter')
    pg.click('details summary')
    pg.click('#reset')
    check(pg.locator('dialog[open]').count() == 1 and pg.evaluate('()=>window.orfoBack()') is True and pg.locator('dialog[open]').count() == 0, 'back closes an open dialog')
    check(not errs, f'android bridge: no page errors {errs}')
    ctx.close()

    # ---------- Сайт: манифест, офлайн ----------
    server = serve()
    url = f'http://127.0.0.1:{server.server_address[1]}/'
    ctx, pg, errs = fresh(url=url)
    check(pg.locator('link[rel=manifest]').count() == 1, 'manifest linked on http')
    manifest = pg.evaluate('()=>fetch("manifest.webmanifest").then(r=>r.json())')
    check(manifest['display'] == 'standalone' and len(manifest['icons']) == 3, 'manifest is valid')
    pg.wait_for_function('()=>navigator.serviceWorker.ready.then(()=>true)')
    pg.reload()
    pg.wait_for_function('()=>!!navigator.serviceWorker.controller')
    pg.click('#mixed'); solve(pg)
    ctx.set_offline(True)
    pg.reload()
    check(pg.locator('#app h2').first.is_visible() and pg.evaluate('()=>db.attempts.length') == 1, 'works offline after first visit')
    ctx.set_offline(False)
    check(not [e for e in errs if 'net::ERR_INTERNET_DISCONNECTED' not in e], f'site: no console errors {errs}')
    ctx.close()
    server.shutdown()

    ctx = browser.new_context(viewport={'width': 390, 'height': 844})
    pg = ctx.new_page(); pg.goto(FILE_URL)
    check(pg.evaluate('()=>document.documentElement.scrollWidth') <= 390, 'no horizontal scroll on phone')
    pg.screenshot(path=str(SHOTS / 'home-mobile.png'))
    ctx.close()
    browser.close()

print('\nFAILURES:', fails if fails else 'none')
sys.exit(1 if fails else 0)
