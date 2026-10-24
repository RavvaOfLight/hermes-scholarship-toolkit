// node --test tests/  — label-by-label checks of what the companion would put in each box.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as M from "../extension/matcher.js";

const schema = M.indexSchema(JSON.parse(readFileSync(new URL("../extension/schema.json", import.meta.url))));

// A made-up student. Nothing here is real.
const data = {
  values: {
    "student.first_name": "Ava", "student.middle_name": "Marie", "student.last_name": "Testerson",
    "student.date_of_birth": "2009-04-15", "student.gender": "Female", "student.citizenship": "U.S. citizen",
    "student.grade_level": "12th grade", "student.email": "ava.t@example.com", "student.phone": "5555550182",
    "student.address_line1": "12 Oak Lane", "student.address_line2": "Apt 3", "student.city": "Testville",
    "student.state": "KS", "student.zip": "12345", "student.county": "Lakeside", "student.country": "United States",
    "parent1.relationship": "Mother", "parent1.first_name": "Maria", "parent1.last_name": "Testerson",
    "parent1.email": "maria@example.com", "parent1.phone": "5555550101", "parent1.occupation": "Nurse",
    "parent1.employer": "Example Health", "parent1.education": "Bachelor's degree",
    "parent2.relationship": "Father", "parent2.first_name": "James", "parent2.last_name": "Testerson",
    "parent2.email": "james@example.com", "parent2.phone": "5555550102", "parent2.occupation": "Electrician",
    "parent2.employer": "Self-employed", "parent2.education": "High school diploma or GED",
    "school.name": "Testville High School", "school.ceeb": "471234", "school.city": "Testville", "school.state": "KS",
    "school.phone": "5555550199", "school.graduation": "2027-05", "school.counselor_first_name": "Dana",
    "school.counselor_last_name": "Lee", "school.counselor_email": "dlee@school.example.org", "school.counselor_phone": "5555550150",
    "academics.gpa_unweighted": "3.92", "academics.gpa_unweighted_scale": "4.0", "academics.gpa_weighted": "4.35",
    "academics.gpa_weighted_scale": "5.0", "academics.class_rank": "12", "academics.class_size": "310",
    "academics.act_composite": "32", "academics.act_english": "33", "academics.act_math": "31", "academics.act_reading": "34",
    "academics.act_science": "30", "academics.act_date": "2026-06-13", "academics.sat_total": "1450", "academics.sat_math": "740",
    "college.name": "Example State University", "college.intended_major": "Mechanical Engineering", "college.career_goal": "Robotics engineer",
    "college.enrollment_status": "Full-time", "household.size": "4", "household.in_college": "1",
    "household.parents_marital_status": "Married", "household.first_generation": "No",
  },
  lists: {
    activities: [
      { name: "Robotics Club", role: "Captain", organization: "Testville HS", grades: "10, 11, 12", hours_per_week: "6", weeks_per_year: "36", description: "Led the build team." },
      { name: "Food Pantry", role: "Volunteer", hours_per_week: "3" },
    ],
    awards: [], work: [],
    recommenders: [{ first_name: "Sam", last_name: "Ortiz", title: "Physics teacher", email: "sortiz@school.example.org", phone: "5555550160" }],
    custom: [{ label: "Student ID", value: "S123456" }, { label: "Church name", value: "Grace Chapel", also: "Place of worship" }],
  },
};

let n = 0;
const box = (label, extra = {}) => ({ ref: `f${++n}`, kind: "text", type: "text", label, alt: [], contexts: [], value: "", ...extra });
const opts = (...texts) => texts.map((t) => ({ text: t, value: t }));
const sel = (label, options, extra = {}) => box(label, { kind: "select", options: opts(...options), selectedIndex: 0, selectDefault: true, ...extra });
const radio = (label, options, extra = {}) => box(label, { kind: "radio", options: opts(...options).map((o) => ({ ...o, checked: false })), ...extra });
const city = box("City");

// One box on a page that also has a City box (so "Address" means the street line).
function one(field) {
  return M.proposeAll([city, field], data, schema).find((r) => r.ref === field.ref);
}
function expectFill(field, item, shown, status = "ready") {
  const row = one(field);
  assert.equal(row.status, status, `${field.label}: ${row.status} — ${row.reason}`);
  assert.equal(row.item, item, `${field.label}: item`);
  if (shown !== undefined) assert.equal(row.display, shown, `${field.label}: value`);
  return row;
}
function expectStatus(field, status) {
  const row = one(field);
  assert.equal(row.status, status, `${field.label}: ${row.status} — ${row.reason} (item ${row.item})`);
  return row;
}

test("the student's own name boxes", () => {
  expectFill(box("First Name"), "student.first_name", "Ava");
  expectFill(box("Legal Last Name *"), "student.last_name", "Testerson");
  expectFill(box("Middle Initial", { maxlength: 1 }), "student.middle_initial", "M");
  expectFill(box("Full Name"), "student.full_name", "Ava Testerson");
  expectFill(box("Name"), "student.full_name", "Ava Testerson");
  expectFill(box("Student's Full Legal Name"), "student.full_name", "Ava Testerson");
  expectFill(box("Applicant name (as it appears on your transcript)"), "student.full_name", "Ava Testerson");
  expectFill(box("Name (Last, First)"), "student.last_first", "Testerson, Ava");
  expectFill(box("Last Name, First Name, MI"), "student.last_first_mi", "Testerson, Ava M");
  expectFill(box("First and last name"), "student.full_name", "Ava Testerson");
  expectFill(box("First", { contexts: ["Your Name"] }), "student.first_name", "Ava");
  expectStatus(box("Preferred name"), "missing");
  expectStatus(box("Maiden name"), "none");
  expectStatus(box("Previous last name"), "none");
});

test("contact boxes, formats and second boxes", () => {
  expectFill(box("Email Address"), "student.email", "ava.t@example.com");
  expectFill(box("Confirm email", { type: "email" }), "student.email", "ava.t@example.com");
  expectFill(box("Cell Phone", { type: "tel" }), "student.phone", "555-555-0182");
  expectFill(box("Phone", { type: "tel", placeholder: "(555) 555-5555" }), "student.phone", "(555) 555-0182");
  expectFill(box("Phone Number", { maxlength: 10 }), "student.phone", "5555550182");
  expectFill(box("Phone", { pattern: "\\d{3}-\\d{3}-\\d{4}" }), "student.phone", "555-555-0182");
  expectStatus(box("Home phone"), "check");
  expectStatus(box("Work Phone"), "none");
  expectStatus(box("Alternate email"), "none");
  expectStatus(box("Email 2"), "none");
  expectStatus(box("Emergency contact phone"), "none");
  expectStatus(box("Employer phone"), "none");
  expectStatus(box("Phone", { type: "email" }), "none");
});

test("address boxes", () => {
  expectFill(box("Street Address"), "student.address_line1", "12 Oak Lane");
  expectFill(box("Home address"), "student.address_line1", "12 Oak Lane");
  expectFill(box("Address Line 2 (Apt, Suite, etc.)"), "student.address_line2", "Apt 3");
  expectFill(box("City / Town"), "student.city", "Testville");
  expectFill(box("State", { maxlength: 2 }), "student.state", "KS");
  expectFill(sel("State", ["-- Select --", "Kentucky", "Kansas", "Washington", "Arkansas"]), "student.state", "Kansas");
  expectFill(sel("State of residence", ["Select", "KS", "KY", "AR"]), "student.state", "KS");
  expectFill(box("ZIP Code"), "student.zip", "12345");
  expectFill(box("County of residence"), "student.county", "Lakeside");
  expectStatus(box("Previous address"), "none");
  expectStatus(box("Billing ZIP code"), "none");
  expectStatus(box("Business address"), "none");
  expectStatus(box("State of birth"), "none");
  // a lone "Address" with no City box on the page means the whole address
  const row = M.proposeAll([box("Address")], data, schema)[0];
  assert.equal(row.item, "student.address_full");
  assert.equal(row.display, "12 Oak Lane, Apt 3, Testville, KS 12345");
  assert.equal(row.status, "check");
});

test("dates follow the box's own format and never guess a missing day", () => {
  expectFill(box("Date of Birth", { placeholder: "MM/DD/YYYY" }), "student.date_of_birth", "04/15/2009");
  expectFill(box("Date of birth", { type: "date" }), "student.date_of_birth", "2009-04-15");
  expectFill(box("Birthdate (YYYY-MM-DD)"), "student.date_of_birth", "2009-04-15");
  expectFill(box("Date of birth"), "student.date_of_birth", "04/15/2009", "check");
  const month = sel("Month", ["Month", ...["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]], { group: "Date of Birth" });
  expectFill(month, "student.date_of_birth", "April");
  const day = sel("Day", ["Day", ...Array.from({ length: 31 }, (_, i) => String(i + 1))], { group: "Date of Birth" });
  expectFill(day, "student.date_of_birth", "15");
  const year = sel("Year", ["Year", ...Array.from({ length: 20 }, (_, i) => String(2000 + i))], { group: "Date of Birth" });
  expectFill(year, "student.date_of_birth", "2009");
  expectFill(box("Date of Birth", { placeholder: "MM", maxlength: 2 }), "student.date_of_birth", "04");
  expectStatus(box("Month"), "none");
  expectFill(box("Graduation date", { placeholder: "MM/YYYY" }), "school.graduation", "05/2027");
  expectFill(box("Expected graduation year"), "school.graduation_year", "2027");
  expectFill(sel("Class of", ["2025", "2026", "2027", "2028"]), "school.graduation_year", "2027");
  expectStatus(box("High school graduation date", { type: "date" }), "none");
  expectFill(box("ACT test date", { placeholder: "mm/dd/yyyy" }), "academics.act_date", "06/13/2026");
});

test("choices match exactly: Female never becomes Male, and no fuzzy picks", () => {
  expectFill(sel("Gender", ["Select", "Male", "Female", "Other"]), "student.gender", "Female");
  expectFill(sel("Gender", ["Man", "Woman", "Non-binary"]), "student.gender", "Woman");
  expectFill(radio("Sex", ["M", "F"]), "student.gender", "F");
  expectStatus(sel("Gender", ["Male", "Other"]), "none");
  expectStatus(sel("Gender identity", ["Cisgender male", "Cisgender female", "Transgender"]), "none");
  expectFill(radio("Are you a U.S. citizen?", ["Yes", "No"]), "student.us_citizen", "Yes");
  expectFill(sel("Citizenship status", ["U.S. Citizen", "Permanent Resident", "Other"]), "student.citizenship", "U.S. Citizen");
  expectFill(sel("Current grade level", ["9", "10", "11", "12"]), "student.grade_level", "12");
  expectFill(sel("Grade", ["Freshman", "Sophomore", "Junior", "Senior"]), "student.grade_level", "Senior");
  expectFill(radio("Enrollment status", ["Full-time", "Part-time"]), "college.enrollment_status", "Full-time");
  assert.equal(M.matchOption(opts("Female", "Male"), ["male"]), 1);
  assert.equal(M.matchOption(opts("Female"), ["male"]), null);
  assert.equal(M.matchOption(opts("Arkansas"), ["ks", "kansas"]), null);
  assert.equal(M.matchOption(opts("12", "Senior"), ["12", "senior"]), -1);
});

test("parents: numbered sections, mother/father, and unnumbered ones", () => {
  expectFill(box("First Name", { contexts: ["Parent/Guardian 1 Information"] }), "parent1.first_name", "Maria");
  expectFill(box("Email", { type: "email", contexts: ["Parent/Guardian #2"] }), "parent2.email", "james@example.com");
  expectFill(box("Mother's Name"), "parent1.full_name", "Maria Testerson");
  expectFill(box("Father's Occupation"), "parent2.occupation", "Electrician");
  expectFill(box("Father/Guardian Employer"), "parent2.employer", "Self-employed");
  expectFill(box("Parent 2 First Name"), "parent2.first_name", "James");
  expectFill(box("Second parent last name"), "parent2.last_name", "Testerson");
  expectFill(sel("Parent 1 Highest Level of Education", ["High School", "Some College", "Bachelor's Degree", "Master's Degree"]), "parent1.education", "Bachelor's Degree");
  expectFill(sel("Highest level of education", ["High School", "Some College", "Bachelor's Degree"], { contexts: ["Parent/Guardian 2"] }), "parent2.education", "High School");
  expectFill(sel("Relationship to applicant", ["Mother", "Father", "Guardian", "Other"], { contexts: ["Parent/Guardian 1"] }), "parent1.relationship", "Mother");
  assert.deepEqual(expectStatus(box("Your email", { contexts: ["Parent/Guardian Information"] }), "choose").choices, ["student.email", "parent1.email"]);
  expectFill(box("Student email", { contexts: ["Parent/Guardian Information"] }), "student.email", "ava.t@example.com");
  expectStatus(box("Mother's maiden name"), "none");
  expectStatus(box("Parent date of birth"), "none");
  expectStatus(box("Grandparent's name"), "none");
  // one unnumbered parent box while both parents are saved: offered, but unticked
  expectFill(box("Parent/Guardian Name"), "parent1.full_name", "Maria Testerson", "check");
  // two unnumbered parent sections: the first is parent 1, the second parent 2
  const fields = [
    box("First name", { contexts: ["Parent/Guardian Information"], contextIds: ["h1"] }),
    box("Email", { contexts: ["Parent/Guardian Information"], contextIds: ["h1"] }),
    box("First name", { contexts: ["Parent/Guardian Information"], contextIds: ["h2"] }),
    box("Email", { contexts: ["Parent/Guardian Information"], contextIds: ["h2"] }),
  ];
  const rows = M.proposeAll(fields, data, schema);
  assert.deepEqual(rows.map((r) => r.item), ["parent1.first_name", "parent1.email", "parent2.first_name", "parent2.email"]);
  assert.ok(rows.every((r) => r.status === "check"));
  // repeated unnumbered labels on one page
  const twice = M.proposeAll([box("Parent/Guardian Name"), box("Parent/Guardian Name")], data, schema);
  assert.deepEqual(twice.map((r) => r.item), ["parent1.full_name", "parent2.full_name"]);
});

test("counselor, school and recommenders", () => {
  expectFill(box("Counselor Name"), "school.counselor_full_name", "Dana Lee");
  expectFill(box("School Counselor Email"), "school.counselor_email", "dlee@school.example.org");
  expectFill(box("Guidance counselor phone"), "school.counselor_phone", "555-555-0150");
  expectFill(box("Email", { contexts: ["School Counselor"] }), "school.counselor_email", "dlee@school.example.org");
  expectFill(box("High School Name"), "school.name", "Testville High School");
  expectFill(box("What is the name of the high school you currently attend?"), "school.name", "Testville High School");
  expectFill(box("School"), "school.name", "Testville High School", "check");
  expectFill(box("CEEB Code"), "school.ceeb", "471234");
  expectFill(box("School phone"), "school.phone", "555-555-0199");
  expectFill(box("High school city"), "school.city", "Testville");
  expectFill(box("High school cumulative GPA (4.0 scale)"), "academics.gpa_unweighted", "3.92");
  expectStatus(box("College city"), "none");
  expectStatus(box("Organization name"), "none");
  expectStatus(box("Team name"), "none");
  expectStatus(box("School email"), "none");
  expectStatus(box("Middle school name"), "none");
  expectFill(box("Email", { contexts: ["Recommender 1"] }), "recommenders.0.email", "sortiz@school.example.org");
  expectFill(box("Recommender full name"), "recommenders.0.full_name", "Sam Ortiz", "check");
  expectStatus(box("Teacher's email"), "none");
  expectStatus(box("Principal's name"), "none");
});

test("GPA and test scores pick the right number or ask", () => {
  expectFill(box("Cumulative GPA (4.0 scale)"), "academics.gpa_unweighted", "3.92");
  expectFill(box("Weighted GPA"), "academics.gpa_weighted", "4.35");
  expectFill(box("GPA (on a 5.0 scale)"), "academics.gpa_weighted", "4.35");
  expectFill(box("Unweighted GPA (out of 4.0)"), "academics.gpa_unweighted", "3.92");
  expectStatus(box("GPA"), "choose");
  expectStatus(box("GPA scale"), "choose");
  expectStatus(box("College GPA"), "none");
  expectStatus(box("Unweighted GPA", { type: "number" }), "none"); // whole numbers only
  expectFill(box("Unweighted GPA", { type: "number", step: "0.01" }), "academics.gpa_unweighted", "3.92");
  expectStatus(box("GPA (6.0 scale)"), "none");
  expectFill(box("Class Rank"), "academics.class_rank", "12");
  expectFill(box("Class size"), "academics.class_size", "310");
  expectFill(box("out of", { prev: "Class rank" }), "academics.class_size", "310", "check");
  expectStatus(box("out of"), "none");
  expectFill(box("ACT Composite Score"), "academics.act_composite", "32");
  expectFill(box("Highest ACT score"), "academics.act_composite", "32");
  expectFill(box("ACT Math"), "academics.act_math", "31");
  expectFill(box("SAT Total"), "academics.sat_total", "1450");
  expectStatus(box("ACT superscore"), "none");
  expectStatus(box("ACT/SAT Score"), "choose");
  expectStatus(box("PSAT score"), "none");
  expectStatus(box("Expected ACT score"), "none");
  expectStatus(box("ACT score", { type: "number", max: "30" }), "none");
});

test("college plans and household", () => {
  expectFill(box("Intended Major"), "college.intended_major", "Mechanical Engineering");
  expectFill(box("What do you plan to study?"), "college.intended_major", "Mechanical Engineering");
  expectFill(box("College you plan to attend"), "college.name", "Example State University");
  expectFill(box("Career goal"), "college.career_goal", "Robotics engineer");
  expectFill(box("Number of people in household"), "household.size", "4");
  expectStatus(box("Number in household (not including yourself)"), "none");
  expectFill(box("Parents' marital status"), "household.parents_marital_status", "Married");
  expectStatus(box("Marital status"), "none");
  expectFill(radio("Are you a first-generation college student?", ["Yes", "No"]), "household.first_generation", "No");
  expectStatus(box("I am a first-generation college student", { kind: "checkbox" }), "done");
  expectStatus(box("Household income"), "missing");
  expectStatus(box("Adjusted gross income"), "missing"); // saved under Financial aid now
});

test("private and agreement boxes are never filled", () => {
  for (const f of [
    box("Social Security Number"), box("SSN (last 4 digits)"), box("Password", { kind: "secret", type: "password" }),
    box("Signature"), box("Type your full name as your electronic signature"), box("FSA ID username"),
    box("I certify that the information above is accurate", { kind: "checkbox" }), box("Transcript", { kind: "file", type: "file" }),
    box("Driver's license number"), box("Bank routing number"),
    box("Essay: Why do you deserve this scholarship?", { kind: "textarea", type: "textarea" }),
    box("Describe your career goals", { kind: "textarea", type: "textarea" }),
    box("Anything else we should know?", { kind: "textarea", type: "textarea" }),
    radio("Do you agree to the terms?", ["Yes", "No"]),
  ]) expectStatus(f, "blocked");
  // even when the user picks an item for it
  assert.equal(M.proposeForItem(box("Social Security Number"), "student.phone", data, schema).status, "blocked");
});

test("activity rows, custom answers and boxes that already have text", () => {
  expectFill(box("Activity 1 Name"), "activities.0.name", "Robotics Club");
  expectFill(box("Activity 2 - Hours per week"), "activities.1.hours_per_week", "3");
  expectFill(box("Hours per week", { contexts: ["Activity 1"] }), "activities.0.hours_per_week", "6");
  expectFill(box("Position/Leadership", { contexts: ["Extracurricular Activity #1"] }), "activities.0.role", "Captain");
  const rows = M.proposeAll([box("Activity name"), box("Activity name"), box("Activity name")], data, schema);
  assert.deepEqual(rows.map((r) => [r.item, r.status]), [["activities.0.name", "check"], ["activities.1.name", "check"], ["activities.2.name", "missing"]]);
  expectFill(box("Student ID"), "custom.0", "S123456");
  expectFill(box("Place of Worship"), "custom.1", "Grace Chapel");
  expectStatus(box("First Name", { value: "Ava" }), "done");
  const has = expectStatus(box("First Name", { value: "Avery" }), "has_value");
  assert.equal(has.display, "Ava");
  expectStatus(box("Pet's name"), "none");
  expectStatus(box("Favorite color"), "none");
});

test("putting a chosen item into a chosen box", () => {
  const r = M.proposeForItem(box("Box 7"), "school.counselor_email", data, schema);
  assert.equal(r.status, "ready");
  assert.equal(r.display, "dlee@school.example.org");
  assert.equal(M.proposeForItem(box("Contact", { type: "tel" }), "student.email", data, schema).status, "none");
  assert.equal(M.proposeForItem(sel("Pick", ["A", "B"]), "student.first_name", data, schema).status, "none");
  const date = M.proposeForItem(box("Date", { placeholder: "MM/DD/YYYY" }), "student.date_of_birth", data, schema);
  assert.equal(date.display, "04/15/2009");
  assert.equal(M.proposeForItem(box("Long", { maxlength: 5 }), "school.name", data, schema).status, "none");
});

test("telling it what to fill", () => {
  const refs = M.searchItems("parents names", data, schema).map((r) => r.ref);
  for (const r of ["parent1.first_name", "parent1.last_name", "parent2.first_name", "parent1.full_name"]) assert.ok(refs.includes(r), r);
  assert.equal(M.searchItems("counselor email", data, schema)[0].ref, "school.counselor_email");
  assert.ok(M.searchItems("ACT scores", data, schema).some((r) => r.ref === "academics.act_composite"));
  assert.ok(M.searchItems("my gpa", data, schema).some((r) => r.ref === "academics.gpa_unweighted"));
  assert.ok(M.searchItems("student id", data, schema).some((r) => r.ref === "custom.0"));
  assert.deepEqual(M.searchItems("zzz", data, schema), []);
});
