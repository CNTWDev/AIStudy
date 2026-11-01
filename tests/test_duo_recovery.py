"""Exercise the real duo physics when a moving obstacle traps/ejects a player."""
import subprocess
from pathlib import Path


def test_crush_and_out_of_bounds_trigger_quiz():
    script = Path('app/static/play/duo.js').read_text()
    # Expose closure state only in the test VM; keep the production physics intact.
    script = script.replace('  // ---------------------------------------------------------------- 画', '''
    globalThis.probe = (obstacles, player) => {
      me = Object.assign({role: 'fire', x: 60, y: 60, inv: 0, vx: 0, vy: 0, walk: 0}, player);
      Wd = {g: Array.from({length: ROWS}, () => Array(COLS).fill('.')), gems: new Map(), levers: [], doors: {fire: {c: 28, r: 13}}};
      solids = () => obstacles;
      let deaths = 0;
      fx = {burst() {}, shake() {}};
      mp = {die() {deaths++;}, toast() {}};
      physics(0);
      return {deaths, dead: me.dead};
    };
  // ---------------------------------------------------------------- 画''')
    harness = '''
const assert = require('node:assert/strict');
globalThis.Play = {input: {keys: {}}, sfx: {play() {}}, mount(def) {globalThis.game = def;}};
'''
    harness += "globalThis.PlayShell = {init(def) {globalThis.game = def;}};\n"
    harness += script + '''
assert.equal(probe([], {}).deaths, 0);
assert.equal(probe([], {x: -100}).deaths, 1);
assert.equal(probe([{x: 0, y: 0, w: 960, h: 450}], {}).deaths, 1);
'''
    result = subprocess.run(['node', '-e', harness], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
