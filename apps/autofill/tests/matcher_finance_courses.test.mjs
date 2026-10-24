// node --test tests/  — financial aid boxes, class lists, range dropdowns and custom dropdowns.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as M from "../extension/matcher.js";

const schema = M.indexSchema(JSON.parse(readFileSync(new URL("../extension/schema.json", import.meta.url))));

// A made-up student. Nothing here is real.
const data = {
  values: {
    "student.first_name": "Ava", "student.state": "KS", "student.citizenship": "U.S. citizen",
    "parent1.relationship": "Mother", "parent1.first_name": "Maria",
    "household.size": "4", "household.first_generation": "No",
    "financial.financial_need": "Yes", "financial.applying_for_aid": "Yes", "financial.fafsa_filed": "Yes",
    "financial.fafsa_date": "2025-12-03", "financial.sai": "-1500", "financial.pell_eligible": "Yes",
    "financial.css_profile": "No", "financial.family_income": "52000", "financial.agi": "48750",
    "financial.student_income": "3200", "financial.unmet_need": "9000", "financial.free_lunch": "Yes",
    "academics.gpa_unweighted": "3.92", "academics.gpa_unweighted_scale": "4.0", "academics.class_rank": "12", "academics.class_size": "310",
  },
  lists: {
    courses: [
      { name: "AP Biology", level: "AP", grade_level: "11th grade", grade: "A", credits: "1.0", gpa_points: "5.0", status: "Completed" },
      { name: "English 10 Honors", level: "Honors", grade_level: "10th grade", grade: "A-", credits: "1.0", gpa_points: "4.17", status: "Completed" },
      { name: "College Composition I", level: "Dual enrollment", grade_level: "11th grade", grade: "A", credits: "3.0", status: "Completed" },
      { name: "AP Calculus BC", level: "AP", grade_level: "12th grade", status: "In progress" },
      { name: "Geometry", level: "Regular", grade_level: "9th grade", grade: "B+", credits: "1.0", status: "Completed" },
      { name: "AP Government", level: "AP", grade_level: "12th grade", status: "Planned" },
    ],
    activities: [], awards: [], work: [], recommenders: [], custom: [],
  },
};

let n = 0;
const box = (label, extra = {}) => ({ ref: `f${++n}`, kind: "text", type: "text", label, alt: [], contexts: [], value: "", ...extra });
const opts = (...texts) => texts.map((t) => ({ text: t, value: t }));
const sel = (label, options, extra = {}) => box(label, { kind: "select", options: opts(...options), selectedIndex: 0, selectDefault: true, ...extra });
const radio = (label, options, extra = {}) => box(label, { kind: "radio", options: opts(...options).map((o) => ({ ...o, checked: false })), ...extra });
const one = (field, d = data) => M.proposeAll([field], d, schema)[0];
function expectFill(field, item, shown, status = "ready") {
  const row = one(field);
  assert.equal(row.status, status, `${field.label}: ${row.status} — ${row.reason} (item ${row.item})`);
  assert.equal(row.item, item, `${field.label}: item`);
  if (shown !== undefined) assert.equal(row.display, shown, `${field.label}: value`);
  return row;
}
const expectStatus = (field, status) => {
  const row = one(field);
  assert.equal(row.status, status, `${field.label}: ${row.status} — ${row.reason} (item ${row.item})`);
  return row;
};

test("financial aid questions", () => {
  expectFill(radio("Do you qualify for financial aid?", ["Yes", "No"]), "financial.financial_need", "Yes");
  expectFill(radio("Do you have demonstrated financial need?", ["Yes", "No"]), "financial.financial_need", "Yes");
  expectFill(sel("Are you applying for financial aid?", ["Select", "Yes", "No"]), "financial.applying_for_aid", "Yes");
  expectFill(radio("Have you completed the FAFSA?", ["Yes", "No", "Not yet"]), "financial.fafsa_filed", "Yes");
  expectFill(box("FAFSA submission date", { placeholder: "MM/DD/YYYY" }), "financial.fafsa_date", "12/03/2025");
  expectFill(box("Student Aid Index (SAI)"), "financial.sai", "-1500");
  expectFill(box("SAI"), "financial.sai", "-1500");
  expectFill(box("Expected Family Contribution (EFC)"), "financial.sai", "-1500", "check"); // EFC is the old name: look first
  expectFill(radio("Are you Pell Grant eligible?", ["Yes", "No"]), "financial.pell_eligible", "Yes");
  expectFill(radio("Have you completed the CSS Profile?", ["Yes", "No"]), "financial.css_profile", "No");
  expectFill(box("Total family income"), "financial.family_income", "52000");
  expectFill(box("Annual household income"), "financial.family_income", "52000");
  expectFill(box("Family's annual income"), "financial.family_income", "52000");
  expectFill(box("Parents' adjusted gross income (AGI)"), "financial.agi", "48750");
  expectFill(box("Your annual income"), "financial.student_income", "3200");
  expectFill(box("Unmet financial need"), "financial.unmet_need", "9000");
  expectFill(radio("Do you receive free or reduced lunch?", ["Yes", "No"]), "financial.free_lunch", "Yes");
  expectFill(box("Number of people in household"), "household.size", "4");
});

test("financial questions that need your own words or don't fit are left alone", () => {
  expectStatus(box("Explain your financial need"), "none");
  expectStatus(box("Describe your financial need", { kind: "textarea", type: "textarea" }), "blocked");
  expectStatus(box("Income"), "none"); // whose?
  expectStatus(box("SAI", { type: "number", min: "0" }), "none"); // a negative SAI won't fit a box that starts at 0
  expectStatus(box("Pell Grant"), "check");
});

test("income and other numbers in range dropdowns", () => {
  const income = sel("Total family income", ["Select one", "Less than $30,000", "$30,000 - $49,999", "$50,000 - $74,999", "$75,000 - $99,999", "$100,000 or more", "Prefer not to say"]);
  const row = expectFill(income, "financial.family_income", "$50,000 - $74,999", "check");
  assert.match(row.reason, /falls in this range/);
  expectFill(sel("Annual household income", ["Under 25k", "25k-50k", "50k-75k", "75k+"]), "financial.family_income", "50k-75k", "check");
  // a value on a shared boundary is never guessed
  const edge = sel("Total family income", ["$0-$52,000", "$52,000-$80,000", "Over $80,000"]);
  assert.equal(one(edge).status, "none");
  expectFill(sel("Number of people in household", ["1", "2", "3", "4", "5", "6 or more"]), "household.size", "4");
  const big = { ...data, values: { ...data.values, "household.size": "7" } };
  assert.equal(M.proposeAll([sel("Household size", ["1-2", "3-4", "5-6", "7 or more"])], big, schema)[0].display, "7 or more");
  assert.deepEqual(M.parseRange("$150,000+"), { lo: 150000 });
  assert.deepEqual(M.parseRange("Under $25,000"), { hi: 25000, hiOpen: true });
  assert.equal(M.parseRange("Prefer not to say"), null);
});

test("class rows on a form", () => {
  expectFill(box("Course 1 name"), "courses.0.name", "AP Biology");
  expectFill(box("Course 2 - Final grade"), "courses.1.grade", "A-");
  expectFill(box("Credits", { contexts: ["Course 3"] }), "courses.2.credits", "3.0");
  expectFill(sel("Level", ["Select", "Regular", "Honors", "AP", "IB", "Dual Enrollment"], { contexts: ["Course 3"] }), "courses.2.level", "Dual Enrollment");
  expectFill(sel("Course 1 level", ["Standard", "Honors", "Advanced Placement"]), "courses.0.level", "Advanced Placement");
  expectFill(sel("Course 2 grade level", ["9", "10", "11", "12"]), "courses.1.grade_level", "10");
  expectFill(box("GPA points", { contexts: ["Course 1"] }), "courses.0.gpa_points", "5.0");
  // three unnumbered "Course name" boxes are the first three classes, unticked to check
  const rows = M.proposeAll([box("Course name"), box("Course name"), box("Course name")], data, schema);
  assert.deepEqual(rows.map((r) => [r.item, r.status]), [["courses.0.name", "check"], ["courses.1.name", "check"], ["courses.2.name", "check"]]);
  // "class" alone keeps meaning your class rank or graduating class
  expectFill(box("Class rank"), "academics.class_rank", "12");
  expectFill(box("Class size"), "academics.class_size", "310");
});

test("how many and which classes", () => {
  expectFill(box("Number of AP courses taken", { type: "number" }), "courses.count.ap.any", "2", "check"); // includes one in progress
  expectFill(box("How many honors classes have you taken?"), "courses.count.honors.any", "1");
  expectFill(box("Number of dual enrollment courses"), "courses.count.de.any", "1");
  expectFill(box("List your AP courses", { kind: "textarea", type: "textarea" }), "courses.names.ap.any", "AP Biology, AP Calculus BC", "check");
  expectFill(box("Current courses", { kind: "textarea", type: "textarea" }), "courses.names.all.now", "AP Calculus BC");
  expectFill(box("AP classes completed"), "courses.names.ap.done", "AP Biology");
  const adv = expectFill(box("Number of advanced courses"), "courses.count.advanced.any", "4", "check");
  assert.match(adv.reason, /honors/);
  const none = { ...data, lists: { ...data.lists, courses: [] } };
  assert.equal(M.proposeAll([box("Number of AP courses")], none, schema)[0].status, "missing");
});

test("custom dropdowns", () => {
  // choices unknown until it opens: the panel opens it at Fill time and picks only an exact match
  const combo = box("State", { kind: "combo" });
  const row = one(combo);
  assert.equal(row.status, "check");
  assert.deepEqual(row.action, { kind: "combo" });
  assert.equal(row.display, "Kansas (KS)");
  // when the panel has the live choices it runs the same rules as a normal dropdown
  const live = M.formatFor({ ...combo, kind: "select", options: opts("Kentucky", "Kansas", "Arkansas") }, M.itemInfo("student.state", data, schema), schema);
  assert.deepEqual(live.action, { kind: "select", index: 1 });
  const none = M.formatFor({ ...combo, kind: "select", options: opts("Ohio", "Texas") }, M.itemInfo("student.state", data, schema), schema);
  assert.ok(none.fail);
  // choices already in the page (Google Forms style): matched before you press Fill
  const gf = box("Citizenship status", { kind: "combo", options: opts("Choose", "U.S. Citizen", "Permanent Resident", "Other") });
  const gr = one(gf);
  assert.equal(gr.status, "ready");
  assert.deepEqual(gr.action, { kind: "combo", text: "U.S. Citizen" });
});

test("exam scores", () => {
  const d = { ...data, lists: { ...data.lists, exams: [{ exam: "AP Chemistry", score: "5" }, { exam: "AP Calculus BC", score: "4" }] } };
  const one2 = (f) => M.proposeAll([f], d, schema)[0];
  const a = one2(box("List your AP exam scores", { kind: "textarea", type: "textarea" }));
  assert.deepEqual([a.status, a.display], ["ready", "AP Chemistry (5), AP Calculus BC (4)"]);
  const b = one2(box("Exam 2 score"));
  assert.deepEqual([b.item, b.display], ["exams.1.score", "4"]);
  const c = one2(box("Exam", { contexts: ["Exam 1"] }));
  assert.deepEqual([c.item, c.display], ["exams.0.exam", "AP Chemistry"]);
});
