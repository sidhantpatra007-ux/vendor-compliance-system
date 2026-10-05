import { test } from 'node:test';
import assert from 'node:assert/strict';
import { csvCell, delta, intakeState, isClosed, isOpen, normalizeVendor, safeExternal, timestamp } from '../src/data.js';
test('null scores remain unassessed and false answers remain supplied', () => {
  assert.equal(delta([{score:null},{score:100}]), null);
  assert.equal(intakeState({}), 'Not supplied');
  assert.equal(intakeState({processes_personal_data:false}), 'Intake incomplete');
  const row=normalizeVendor({vendor_id:1,latest_score:null},undefined,[]);
  assert.equal(row.latest_score,null);assert.equal(row.criticality,'Unknown');
});
test('CSV keeps leading zeroes and neutralises formula cells', () => {
  assert.equal(csvCell('00000006'), '"00000006"');
  assert.equal(csvCell('=1+1'), '"\'=1+1"');
  assert.equal(csvCell('hello,"world"'), '"hello,""world"""');
});
test('naive backend timestamps are treated as UTC', () => {
  assert.equal(timestamp('2026-09-26T19:47:18.193252').toISOString(),'2026-09-26T19:47:18.193Z');
  assert.equal(timestamp('2026-09-26T20:47:18+01:00').toISOString(),'2026-09-26T19:47:18.000Z');
  assert.equal(timestamp('nonsense'),null);
});
test('unsafe source links and closed alerts cannot be treated as open', () => {
  assert.equal(safeExternal('javascript:alert(1)'),null);
  assert.equal(safeExternal('/relative'),null);
  assert.equal(isOpen({status:'resolved'}),false);
  assert.equal(isClosed({status:'resolve'}),true);
});
