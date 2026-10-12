/* Parent-facing course choices. Selecting a scheme is explicit; grade changes preserve courses. */
(() => {
  const grade = document.getElementById('grade');
  const cards = [...document.querySelectorAll('.course-choice')];
  if (!grade || !cards.length) return;
  const rank = () => Number(grade.selectedOptions[0]?.dataset.rank || 0);
  const inRange = option => rank() >= Number(option.dataset.start) && rank() <= Number(option.dataset.end) + 0.5;
  let lastGrade = grade.value;
  const update = () => {
    const gradeChanged = grade.value !== lastGrade;
    cards.forEach(card => {
      const select = card.querySelector('.course-select');
      card.querySelectorAll('.course-info').forEach(info => {
        const active = info.dataset.pack === select.value;
        const track = info.querySelector('.course-track');
        if (active && track && (info.hidden || gradeChanged)) track.open = rank() >= 9 || track.dataset.alwaysOpen === '1';
        info.hidden = !active;
        info.querySelectorAll('select, input').forEach(input => input.disabled = !active);
        if (!active) info.querySelectorAll('details').forEach(details => details.open = false);
      });
      const option = select.selectedOptions[0];
      const status = card.querySelector('.picker-status');
      status.textContent = !select.value || inRange(option) ? '' :
        (rank() < Number(option.dataset.start) ? '这门课从更高学段开始；确认是提前学习后再选。' : '当前年级已超过这门课的覆盖范围；可用于复习，请核对是否符合需要。');
    });
    lastGrade = grade.value;
  };
  cards.forEach(card => card.querySelector('.course-select').addEventListener('change', update));
  grade.addEventListener('change', update);
  const preset = document.getElementById('preset');
  preset?.addEventListener('change', () => {
    const feedback = document.getElementById('preset-feedback');
    const data = preset.selectedOptions[0]?.dataset.packs;
    if (!data) {
      feedback.textContent = '可以逐科选择，已选教材已保留。';
      return;
    }
    if (!inRange(preset.selectedOptions[0])) {
      preset.value = '';
      feedback.textContent = '这个学校方案不适用于当前年级，未套用；已选教材已保留，请逐科核对。';
      return;
    }
    cards.forEach(card => card.querySelector('.course-select').value = '');
    let count = 0;
    for (const [subject, choice] of Object.entries(JSON.parse(data))) {
      const card = cards.find(c => c.dataset.subject === subject);
      if (!card) continue;
      const select = card.querySelector('.course-select');
      const pid = typeof choice === 'string' ? choice : choice.pack;
      const option = [...select.options].find(o => o.value === pid);
      if (!option || !inRange(option)) continue;
      select.value = pid;
      const track = document.getElementById(`track-${pid}`);
      if (track && choice.track) track.value = choice.track;
      count++;
    }
    update();
    feedback.textContent = count ? `已按当前年级预填 ${count} 科。学校方案仅作起点，请按课本逐科核对；未覆盖的科目暂不安排。` :
      '这个方案没有覆盖当前年级的可选课程，请逐科选择或更换学校方案。';
  });
  update();
})();
