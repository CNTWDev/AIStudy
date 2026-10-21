/* 选课清单（_course_grid.html）：一门课一行一个开关。开了才提交这门课的教材、方向、考试日期；
   有几套教材的，下拉框里换哪套，下面的方向、日期、核对说明跟着换。学校方案、添加孩子页的默认课都经 coursePicker.set 填进来。 */
(() => {
  const grade = document.getElementById('grade');
  const rows = [...document.querySelectorAll('.cpick')];
  if (!rows.length) return;
  const rank = () => Number(grade?.selectedOptions[0]?.dataset.rank || 0);
  const sw = row => row.querySelector('.cp-on');
  const packSel = row => row.querySelector('.cp-pack');
  const source = row => packSel(row)?.selectedOptions[0] || sw(row);  // 带 data-start/end 的那个
  const value = row => sw(row).checked ? (packSel(row) ? packSel(row).value : sw(row).value) : '';
  const inRange = el => rank() >= Number(el.dataset.start) && rank() <= Number(el.dataset.end) + 0.5;

  const render = row => {
    const on = sw(row).checked, pid = packSel(row)?.value || sw(row).value;
    row.toggleAttribute('data-on', on);
    const opts = row.querySelector('.cp-opts');
    if (packSel(row)) packSel(row).disabled = !on;
    opts.querySelectorAll('[data-pack]').forEach(el => {
      const show = el.dataset.pack === pid && (!el.dataset.from || rank() >= Number(el.dataset.from));
      el.hidden = !show;
      // 只提交当前这套的方向和日期；没显示出来的方向（还没到备考年级）仍按默认提交
      el.querySelectorAll('select, input').forEach(x => x.disabled = !on || el.dataset.pack !== pid);
    });
    const note = opts.querySelector('.cp-note:not([data-pack])'), src = source(row);
    note.textContent = !on || inRange(src) ? '' : rank() < Number(src.dataset.start)
      ? '这门课从更高年级开始，确认是提前学再打开。' : '这个年级已经超出这门课的范围，可以拿来复习。';
    note.hidden = !note.textContent;
    opts.hidden = !on || ![...opts.children].some(el => !el.hidden && el.tagName !== 'DETAILS');
    if (!on) opts.querySelector('details')?.removeAttribute('open');
  };
  const update = () => rows.forEach(render);
  const changed = row => { render(row); row.dispatchEvent(new Event('course-change', {bubbles: true})); };
  rows.forEach(row => {
    sw(row).addEventListener('change', () => changed(row));
    packSel(row)?.addEventListener('change', () => changed(row));
  });
  grade?.addEventListener('change', update);

  // 给别的脚本用：读、写一门课（pid 为空 = 不学），track 可选
  const set = (subject, pid, track) => {
    const row = rows.find(r => r.dataset.subject === subject);
    if (!row) return false;
    if (pid && packSel(row)) {
      if (![...packSel(row).options].some(o => o.value === pid)) return false;
      packSel(row).value = pid;
    } else if (pid && sw(row).value !== pid) return false;
    sw(row).checked = !!pid;
    const t = pid && document.getElementById('track-' + pid);
    if (t && track) t.value = track;
    render(row);
    return true;
  };
  const chosen = () => rows.filter(value).map(row => ({row, subject: row.dataset.subject, name: row.dataset.name,
    pack: value(row), label: (packSel(row)?.selectedOptions[0].textContent || row.querySelector('.cp-sub').textContent).split('（')[0]}));
  window.coursePicker = {rows, set, chosen, update, clear: () => rows.forEach(r => set(r.dataset.subject, ''))};

  // 家长编辑孩子：学校方案一键预填（只填这个年级在范围内的课）
  const preset = document.getElementById('preset');
  preset?.addEventListener('change', () => {
    const feedback = document.getElementById('preset-feedback');
    const data = preset.selectedOptions[0]?.dataset.packs;
    if (!data) { feedback.textContent = '可以逐科选择，已选的课保留。'; return; }
    if (!inRange(preset.selectedOptions[0])) {
      preset.value = '';
      feedback.textContent = '这个学校方案不适用于当前年级，没有套用；已选的课保留，请逐科核对。';
      return;
    }
    window.coursePicker.clear();
    let count = 0;
    for (const [subject, choice] of Object.entries(JSON.parse(data))) {
      const pid = typeof choice === 'string' ? choice : choice.pack;
      const row = rows.find(r => r.dataset.subject === subject);
      const opt = row && (packSel(row) ? [...packSel(row).options].find(o => o.value === pid) : sw(row).value === pid && sw(row));
      if (opt && inRange(opt) && set(subject, pid, choice.track)) count++;
    }
    feedback.textContent = count ? `已按当前年级打开 ${count} 门课。学校方案只是起点，请按课本逐科核对。` :
      '这个方案没有覆盖当前年级的课，请逐科选择或换一个学校方案。';
  });
  update();
})();
