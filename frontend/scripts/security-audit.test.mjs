import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { ADVISORY, evaluateAudit } from "./security-audit.mjs";

const policy = JSON.parse(readFileSync(new URL("../security-exception.json", import.meta.url)));
const now = Date.parse("2026-11-18T12:00:00Z");
function fixture() {
  const packages = Object.fromEntries(Object.entries(policy.versions).map(([name, version]) => ["node_modules/" + name, { version, dev: true }]));
  const direct = { name: "picomatch", url: ADVISORY, severity: "high" };
  const via = { picomatch: [direct], fdir: ["picomatch"], tinyglobby: ["fdir", "picomatch"], vite: ["fdir", "picomatch", "tinyglobby"] };
  const vulnerabilities = Object.fromEntries(Object.entries(via).map(([name, causes]) => [name, { name, severity: "high", nodes: ["node_modules/" + name], via: causes }]));
  return { report: { auditReportVersion: 2, vulnerabilities }, lock: { packages } };
}
test("only the exact development-only advisory chain is accepted", () => {
  const f = fixture(); assert.equal(evaluateAudit(f.report, f.lock, policy, now).findings, 4);
});
test("a new advisory in the same package fails", () => {
  const f = fixture(); f.report.vulnerabilities.picomatch.via.push({name:"picomatch",url:"https://example.invalid/new",severity:"high"});
  assert.throws(() => evaluateAudit(f.report, f.lock, policy, now), /New or changed/);
});
test("a new affected package fails", () => {
  const f = fixture(); f.report.vulnerabilities.other = {name:"other",severity:"high",nodes:["node_modules/other"],via:["picomatch"]};
  assert.throws(() => evaluateAudit(f.report, f.lock, policy, now), /Unaccepted/);
});
test("production exposure fails even with the same advisory", () => {
  const f = fixture(); delete f.lock.packages["node_modules/picomatch"].dev;
  assert.throws(() => evaluateAudit(f.report, f.lock, policy, now), /production exposure/);
});
test("changed version or nested installation fails", () => {
  for (const mode of ["version","path"]) {
    const f = fixture();
    if(mode==="version") f.lock.packages["node_modules/picomatch"].version="4.0.8";
    else f.report.vulnerabilities.picomatch.nodes=["node_modules/other/node_modules/picomatch"];
    assert.throws(() => evaluateAudit(f.report, f.lock, policy, now), /Unaccepted/);
  }
});
test("expiry and invalid or excessive validity periods fail", () => {
  const f = fixture();
  assert.throws(() => evaluateAudit(f.report, f.lock, policy, Date.parse(policy.expires_utc)), /expired/);
  for (const expires_utc of ["not-a-date","2027-01-01T00:00:00Z"]) {
    assert.throws(() => evaluateAudit(f.report, f.lock, {...policy,expires_utc},now), /invalid/);
  }
});
test("malformed reports, cycles and critical escalation fail", () => {
  const f = fixture(); assert.throws(() => evaluateAudit({...f.report,error:{}},f.lock,policy,now), /Invalid/);
  assert.throws(() => evaluateAudit({auditReportVersion:2,vulnerabilities:"invalid"},f.lock,policy,now), /Invalid/);
  assert.throws(() => evaluateAudit({...f.report,metadata:{vulnerabilities:{high:0,critical:0}}},f.lock,policy,now), /Invalid/);
  f.report.vulnerabilities.picomatch.via=["vite"];
  assert.throws(() => evaluateAudit(f.report,f.lock,policy,now),/Cyclic/);
  const c=fixture();c.report.vulnerabilities.picomatch.severity="critical";
  assert.throws(() => evaluateAudit(c.report,c.lock,policy,now),/Unaccepted/);
});
test("a clean audit passes without using an expired exception", () => {
  const f=fixture();assert.equal(evaluateAudit({auditReportVersion:2,vulnerabilities:{}},f.lock,policy,Date.parse("2030-01-01T00:00:00Z")).findings,0);
});
