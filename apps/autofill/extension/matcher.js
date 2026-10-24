// Decides which saved answer belongs in a form box, and how to type it there.
// Pure functions only (no DOM, no network) so every rule is unit-tested in Node.
//
// The rule of thumb is "never guess": a box gets an answer ready to fill only when its
// label names one saved item for one person and nothing in the label changes the meaning.
// Anything less certain is shown as "check" (unticked) or left for the user to choose.

// ------------------------------------------------------------------ text helpers

export function normalize(text) {
  return String(text ?? "")
    .normalize("NFKD").replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/&/g, " and ")
    .replace(/['‘’`]/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

const STOP = new Set(("a an the of for in on at to by with and or is are be am if as it its this that these those " +
  "please enter type provide write list give select choose pick indicate specify here below above what which " +
  "who whom whose when where how do does did will would should can could may might must has have had any all " +
  "only also e g eg i ie etc required optional field box answer s current currently click mark one each per " +
  "information info").split(" "));

export function tokens(text) {
  return normalize(text).split(" ").filter(Boolean);
}

function contentTokens(text) {
  return tokens(text).filter((t) => !STOP.has(t));
}

const isNumber = (t) => /^\d+$/.test(t);
const words = (s) => new Set(s.split(/\s+/).filter(Boolean));

// ------------------------------------------------------------------ who a box is about

const SUBJECT = {
  counselor: words("counselor counsellor counselors counsellors guidance"),
  recommender: words("recommender recommenders reference references recommendation recommendations referee referees recommending"),
  parent: words("parent parents guardian guardians parental mother mothers mom moms father fathers dad dads " +
    "stepmother stepmothers stepfather stepfathers stepparent stepparents grandmother grandmothers grandfather grandfathers"),
  other: words("teacher teachers coach coaches mentor mentors advisor advisors adviser advisers principal principals " +
    "supervisor supervisors manager managers friend friends pastor pastors minister aunt aunts uncle uncles cousin cousins " +
    "grandparent grandparents"),
  school: words("school schools"),
  college: words("college colleges university universities institution postsecondary"),
  student: words("student students applicant applicants your you yourself nominee candidate"),
};
const PARENT_INDEX_WORDS = { 1: words("1 1st one i first primary"), 2: words("2 2nd two ii second secondary other") };
const PARENT_REL = {
  mother: ["Mother", "Stepmother"], mothers: ["Mother", "Stepmother"], mom: ["Mother", "Stepmother"], moms: ["Mother", "Stepmother"],
  stepmother: ["Stepmother"], stepmothers: ["Stepmother"],
  father: ["Father", "Stepfather"], fathers: ["Father", "Stepfather"], dad: ["Father", "Stepfather"], dads: ["Father", "Stepfather"],
  stepfather: ["Stepfather"], stepfathers: ["Stepfather"],
  grandmother: ["Grandmother"], grandmothers: ["Grandmother"], grandfather: ["Grandfather"], grandfathers: ["Grandfather"],
};

const LIST_WORDS = {
  activities: words("activity activities extracurricular extracurriculars club clubs involvement volunteer volunteering service"),
  awards: words("award awards honor honors honour honours recognition recognitions achievement achievements"),
  work: words("job jobs employment work paid"),
  courses: words("course courses coursework classes"),
  exams: words("exam exams"),
};
const COUNT_WORDS = words("number many count total");
const ORDINALS = { first: 1, second: 2, third: 3, fourth: 4, fifth: 5, sixth: 6, seventh: 7, eighth: 8, ninth: 9, tenth: 10,
  "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5, "6th": 6, "7th": 7, "8th": 8, "9th": 9, "10th": 10,
  one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10 };

// Words that change a label's meaning: "previous address", "work phone", "number in household (not including you)".
const EXPLAIN = words("explain describe elaborate discuss justify why");
const RISKY = words("previous former prior past maiden alias billing shipping business work office employer company " +
  "alternate alternative secondary other emergency temporary temp spouse sibling siblings brother brothers sister sisters " +
  "child children son sons daughter daughters not excluding except without besides superscore super psat preact pre " +
  "practice predicted projected expected anticipated estimated estimate college university middle elementary junior " +
  "graduate transfer birth nickname");

// ------------------------------------------------------------------ what a box asks for

const NAME_OK = "legal full official complete given name names appears appear shown printed print records documents transcript exactly spelled spelling";
const ADDR_OK = "home mailing permanent street residential physical address line residence local primary legal number";
const PLACE_OK = "home mailing permanent residence residential legal";
const SCORE_OK = "score scores highest best composite total combined most recent official test exam taken";
const DATE_OK = "mm dd yyyy yy m d y format date month day year";
const GPA_OK = "cumulative overall unweighted weighted academic total high school scale point grade average most recent latest final official gpa g p a out 0 4 5";

// kind: who the item can belong to. person = student/parent/counselor/recommender, place = student/school address,
// student/school/academic/college/household = the student's own, list:<id> = a row of a saved list.
const ATTRS = [
  { id: "last_first_mi", kind: "person", phrases: ["last first mi", "last first middle initial", "last name first name middle initial", "last name first name mi", "last first middle", "last name first name middle", "last name first middle initial"], allow: NAME_OK },
  { id: "last_first", kind: "person", phrases: ["last name first name", "last first", "last name first", "surname first name", "family name given name"], allow: NAME_OK },
  { id: "full_name_middle", kind: "person", phrases: ["first middle last", "first name middle name last name", "first middle last name", "first name middle initial last name", "first mi last", "full name including middle name", "full name including middle"], allow: NAME_OK },
  { id: "preferred_name", kind: "person", phrases: ["preferred name", "preferred first name", "chosen name", "name you go by", "go by", "goes by", "name you prefer", "prefer to be called", "nickname", "nick name"], allow: NAME_OK + " nickname" },
  { id: "middle_initial", kind: "person", phrases: ["middle initial", "mi", "m i", "middle init", "initial middle"], allow: NAME_OK },
  { id: "first_name", kind: "person", phrases: ["first name", "given name", "legal first name", "first legal name", "fname", "forename", "first names"], short: ["first"], allow: NAME_OK },
  { id: "middle_name", kind: "person", phrases: ["middle name", "middle names"], short: ["middle"], allow: NAME_OK + " middle" },
  { id: "last_name", kind: "person", phrases: ["last name", "surname", "family name", "legal last name", "last legal name", "lname", "last names"], short: ["last"], allow: NAME_OK },
  { id: "full_name", kind: "person", phrases: ["full name", "legal name", "full legal name", "complete name", "first last name", "first name last name", "first last", "name"], allow: NAME_OK },
  { id: "suffix", kind: "person", phrases: ["suffix", "name suffix", "jr sr", "generational suffix", "suffix jr sr iii"], allow: NAME_OK },
  { id: "email", kind: "person", phrases: ["email address", "email", "e mail address", "e mail", "electronic mail", "email id"], allow: "address primary personal preferred contact valid best confirm re verify retype again correct" },
  { id: "phone", kind: "person", phrases: ["phone", "phone number", "telephone", "telephone number", "cell", "cell phone", "cell number", "cellphone", "mobile", "mobile phone", "mobile number", "contact number", "phone no", "tel", "cellular", "cellular phone"], allow: "number primary preferred contact best main personal no num cell mobile" },
  { id: "relationship", kind: "person", phrases: ["relationship", "relationship to student", "relationship to applicant", "relationship to you", "relation", "relation to student", "relation to applicant", "relationship to nominee", "how do you know student", "how do you know applicant", "how they know you", "how do they know you", "capacity known"], allow: "student applicant" },
  { id: "title", kind: "person", phrases: ["title", "professional title", "title position", "position title"], allow: "" },
  { id: "occupation", kind: "person", phrases: ["occupation", "job title", "profession", "job", "occupation job title", "type of work", "line of work"], allow: "" },
  { id: "employer", kind: "person", phrases: ["employer", "employer name", "name of employer", "place of employment", "company", "company name", "workplace", "where employed", "employed by"], allow: "" },
  { id: "education", kind: "person", phrases: ["highest level of education", "education level", "highest education", "level of education", "highest degree", "highest degree earned", "highest degree completed", "education", "highest grade completed", "educational background", "educational level", "highest level of school completed", "level of schooling", "highest education level completed", "highest level of education completed"], allow: "completed earned attained" },

  { id: "address_full", kind: "place", phrases: ["full address", "complete address", "street address city state zip", "address city state zip", "street city state zip", "mailing address city state zip", "address including city state and zip", "address city state zip code", "street address city state zip code"], allow: ADDR_OK },
  { id: "address_line2", kind: "place", phrases: ["address line 2", "address 2", "address2", "street address line 2", "apt", "apartment", "unit", "suite", "apt suite", "apt unit", "apartment number", "apt number", "unit number", "apt no", "suite unit", "apartment suite unit", "apt suite unit", "apartment suite unit building floor"], allow: "number no floor building apt apartment unit suite" },
  { id: "address_line1", kind: "place", phrases: ["street address", "address line 1", "address 1", "address1", "street", "street address line 1", "mailing address", "home address", "permanent address", "address", "residential address", "physical address", "street name and number", "house number and street"], allow: ADDR_OK },
  { id: "city_state_zip", kind: "place", phrases: ["city state zip", "city state zip code", "city state and zip", "city state postal code"], allow: PLACE_OK },
  { id: "city_state", kind: "place", phrases: ["city state", "city and state", "city st"], allow: PLACE_OK },
  { id: "city", kind: "place", phrases: ["city", "town", "city town", "city or town", "town city", "municipality", "city of residence"], allow: PLACE_OK },
  { id: "state", kind: "place", phrases: ["state", "state province", "state of residence", "state territory", "residence state", "state of legal residence", "legal state of residence", "home state"], allow: PLACE_OK },
  { id: "zip", kind: "place", phrases: ["zip", "zip code", "zipcode", "postal code", "zip postal code", "postal zip code", "postcode"], allow: PLACE_OK + " code" },
  { id: "county", kind: "place", phrases: ["county", "county of residence", "residence county", "parish"], allow: PLACE_OK },
  { id: "country", kind: "place", phrases: ["country", "country of residence", "country region"], allow: PLACE_OK },

  { id: "dob", kind: "student", phrases: ["date of birth", "birth date", "birthdate", "dob", "birthday", "date born", "when were you born"], allow: DATE_OK + " birth" },
  { id: "gender", kind: "student", phrases: ["gender", "sex", "gender identity", "sex gender"], allow: "" },
  { id: "pronouns", kind: "student", phrases: ["pronouns", "pronoun", "personal pronouns", "preferred pronouns"], allow: "preferred" },
  { id: "citizenship_country", kind: "student", phrases: ["country of citizenship", "citizenship country", "nationality", "country citizen"], allow: "" },
  { id: "us_citizen", kind: "student", phrases: ["u s citizen", "us citizen", "united states citizen", "citizen of united states", "citizen of u s", "citizen of us", "citizen", "u s citizen or permanent resident"], allow: "united states" },
  { id: "citizenship", kind: "student", phrases: ["citizenship", "citizenship status", "u s citizenship status", "immigration status", "residency status", "citizenship residency status", "citizenship status u s"], allow: "" },
  { id: "grade_level", kind: "student", phrases: ["current grade", "grade level", "grade", "year in school", "class standing", "current year in school", "current grade level", "grade in school", "academic level", "current academic level", "year in high school", "classification", "grade fall"], allow: "" },

  { id: "ceeb", kind: "school", phrases: ["ceeb", "ceeb code", "school code", "high school code", "act code", "ceeb act code", "college board code", "school ceeb code", "ceeb number", "act school code", "high school ceeb code", "ceeb school code"], allow: "number" },
  { id: "school_name", kind: "school", phrases: ["high school", "high school name", "name of high school", "school name", "name of school", "current school", "school attending", "secondary school", "current high school", "name of current school", "high school attending", "high school currently attending", "high school you attend", "school you attend", "current high school name", "high school attended", "name high school"], weak: ["school"], allow: "attend attending attended full name" },
  { id: "graduation_year", kind: "school", phrases: ["graduation year", "year of graduation", "class of", "grad year", "high school graduation year", "year you will graduate", "graduating class", "graduating class year", "class year", "year of high school graduation", "graduation class", "hs graduation year"], allow: "expected anticipated projected high school" },
  { id: "graduation_month", kind: "school", phrases: ["graduation month", "month of graduation"], allow: "expected anticipated high school" },
  { id: "graduation", kind: "school", phrases: ["graduation date", "date of graduation", "high school graduation date", "graduation", "date of high school graduation", "hs graduation date", "expected graduation", "anticipated graduation"], allow: DATE_OK + " expected anticipated projected high school" },

  { id: "gpa", kind: "academic", phrases: ["gpa", "grade point average", "cumulative gpa", "overall gpa", "current gpa", "high school gpa", "cumulative grade point average", "g p a", "unweighted gpa", "weighted gpa", "gpa unweighted", "gpa weighted"], allow: GPA_OK },
  { id: "gpa_scale", kind: "academic", phrases: ["gpa scale", "scale", "on scale of", "grading scale", "gpa scale used", "scale used", "maximum gpa", "max gpa", "highest possible gpa", "gpa based on scale of"], allow: "gpa 0 4 5" },
  { id: "out_of", kind: "academic", phrases: ["out of", "out"], allow: "" },
  { id: "class_rank", kind: "academic", phrases: ["class rank", "rank", "rank in class", "class ranking", "ranking", "rank in graduating class"], allow: "" },
  { id: "class_size", kind: "academic", phrases: ["class size", "size of class", "number of students in class", "number in class", "students in class", "graduating class size", "senior class size", "total students in class", "number of students in your class", "total class size", "size of graduating class"], allow: "total" },
  { id: "act_english", kind: "academic", phrases: ["act english", "act english score", "english act"], allow: SCORE_OK },
  { id: "act_math", kind: "academic", phrases: ["act math", "act mathematics", "act math score", "math act"], allow: SCORE_OK },
  { id: "act_reading", kind: "academic", phrases: ["act reading", "act reading score", "reading act"], allow: SCORE_OK },
  { id: "act_science", kind: "academic", phrases: ["act science", "act science score", "science act"], allow: SCORE_OK },
  { id: "act_date", kind: "academic", phrases: ["act test date", "act date", "date of act", "act date taken", "date act taken", "date taken act", "act exam date"], allow: DATE_OK + " taken most recent" },
  { id: "act_composite", kind: "academic", phrases: ["act", "act composite", "act score", "act composite score", "composite act", "act total", "act total score", "highest act", "act scores", "composite score act"], allow: SCORE_OK },
  { id: "sat_erw", kind: "academic", phrases: ["sat erw", "sat ebrw", "sat evidence based reading and writing", "sat evidence based reading writing", "sat reading and writing", "sat reading writing", "sat verbal", "sat reading", "sat critical reading", "evidence based reading and writing", "ebrw", "erw"], allow: SCORE_OK },
  { id: "sat_math", kind: "academic", phrases: ["sat math", "sat mathematics", "sat math score", "math sat"], allow: SCORE_OK },
  { id: "sat_date", kind: "academic", phrases: ["sat test date", "sat date", "date of sat", "sat date taken", "date sat taken"], allow: DATE_OK + " taken most recent" },
  { id: "sat_total", kind: "academic", phrases: ["sat", "sat total", "sat score", "sat total score", "total sat", "sat composite", "sat combined", "combined sat", "sat scores", "highest sat"], allow: SCORE_OK },

  { id: "college_name", kind: "college", phrases: ["intended college", "college you plan to attend", "college you will attend", "college attending", "school you plan to attend", "planned college", "college of choice", "name of college", "college name", "university name", "intended institution", "which college", "post secondary institution", "postsecondary institution", "college or university", "college university", "college university name", "name of college or university", "institution name", "institution you plan to attend", "college you are attending", "school you will attend", "intended school", "first choice college", "college choice", "college plan attend", "university plan attend"], weak: ["college", "university", "institution"], allow: "intended planned plan attend attending will choice first name full fall spring next year" },
  { id: "major", kind: "college", phrases: ["intended major", "major", "field of study", "intended field of study", "area of study", "program of study", "planned major", "proposed major", "academic major", "anticipated major", "intended area of study", "major field of study", "intended program of study", "major or field of study", "intended course of study", "course of study", "major area of study", "what do you plan to study", "plan to study"], allow: "intended planned proposed anticipated expected academic primary first choice field college university" },
  { id: "career_goal", kind: "college", phrases: ["career goal", "career goals", "intended career", "career interest", "career objective", "future career", "intended occupation", "career plans", "planned career", "career path", "professional goal", "professional goals", "future occupation", "career aspirations"], weak: ["career"], allow: "intended planned future" },
  { id: "start_term", kind: "college", phrases: ["start term", "entry term", "term of entry", "term you will begin", "starting term", "semester of enrollment", "enrollment term", "term of enrollment", "entering term", "term entering", "start semester", "starting semester", "semester you will begin", "anticipated start term", "expected start term", "planned start term", "anticipated enrollment term"], weak: ["term"], allow: "anticipated expected planned intended college" },
  { id: "degree", kind: "college", phrases: ["degree", "degree sought", "intended degree", "degree pursuing", "degree objective", "degree level", "type of degree", "degree type", "degree you are pursuing", "degree goal", "planned degree", "degree seeking"], allow: "intended planned sought pursuing college" },
  { id: "enrollment_status", kind: "college", phrases: ["enrollment status", "full time or part time", "full time part time", "enrollment type", "attendance status", "planned enrollment status", "enrollment intensity"], weak: ["enrollment"], allow: "planned intended expected college" },

  { id: "household_size", kind: "household", phrases: ["household size", "number in household", "number of people in household", "family size", "persons in household", "people in household", "number of family members", "household members", "number of household members", "total household size", "size of household", "number in family", "total number in household", "total number of people in household", "number of people living in household", "number of persons in household", "total persons in household", "how many people live in household", "how many people in household", "people living in household"], allow: "total including yourself you live living" },
  { id: "in_college", kind: "household", phrases: ["number in college", "number of family members in college", "family members in college", "household members in college", "number in household in college", "how many in college", "number of household members in college", "number of family members attending college", "members of household in college", "number attending college", "number of people in college", "number in college next year", "how many will be in college", "number of household members attending college", "people in household attending college"], allow: "next year including yourself you attending enrolled will" },
  { id: "marital_status", kind: "household", phrases: ["marital status", "parents marital status", "parent marital status", "marital status of parents", "parents current marital status", "parental marital status", "marital status parents"], allow: "parental", needsParent: true },
  { id: "first_generation", kind: "household", phrases: ["first generation", "first generation college student", "first generation student", "first in family to attend college", "first in your family to attend college", "first in family", "first gen", "first generation college", "first person in family to attend college", "first in family to go to college", "first in your family to go to college"], allow: "college student attend go person family" },
  { id: "income", kind: "household", phrases: ["household income", "family income", "annual household income", "total household income", "annual family income", "total family income", "yearly household income", "gross household income", "combined household income", "total annual household income", "family annual income", "household annual income", "annual gross household income", "parents income", "parental income", "combined family income", "familys income", "households income", "familys annual income", "family gross income", "total family gross income", "annual income of household", "income of household", "parents annual income", "parents combined income", "total parental income"], allow: "annual yearly total gross combined approximate estimated estimate before taxes range" },
  { id: "agi", kind: "household", phrases: ["adjusted gross income", "agi", "parents agi", "parents adjusted gross income", "family agi", "household agi", "family adjusted gross income", "household adjusted gross income", "adjusted gross income agi", "agi from tax return", "agi reported on fafsa", "parental agi", "parent agi"], allow: "agi annual yearly total combined tax return fafsa reported irs 1040 line 11" },
  { id: "student_income", kind: "student", phrases: ["your income", "your annual income", "your own income", "student income", "applicant income", "personal income", "student annual income", "your earnings", "student earnings", "income you earn", "your yearly income", "applicants income", "students income"], allow: "annual yearly total gross approximate estimated estimate own" },
  { id: "sai", kind: "household", phrases: ["sai", "student aid index", "student aid index sai", "sai student aid index", "fafsa sai", "sai score", "your sai", "sai number", "sai from fafsa", "student aid index from fafsa"], allow: "fafsa your number score submission summary reported" },
  { id: "efc", kind: "household", phrases: ["efc", "expected family contribution", "expected family contribution efc", "efc sai", "sai efc", "efc or sai", "sai or efc", "expected family contribution or student aid index", "student aid index or expected family contribution", "efc student aid index"], allow: "fafsa your number score reported" },
  { id: "financial_need", kind: "household", phrases: ["financial need", "demonstrate financial need", "demonstrated financial need", "have financial need", "qualify for financial aid", "eligible for financial aid", "qualify for need based aid", "qualify for need based financial aid", "need based aid", "demonstrated need", "do you have financial need", "do you demonstrate financial need", "financial need based", "need based financial aid", "eligible for need based aid", "qualify for financial assistance"], allow: "demonstrate demonstrated have qualify eligible significant based" },
  { id: "applying_for_aid", kind: "household", phrases: ["applying for financial aid", "apply for financial aid", "will you apply for financial aid", "plan to apply for financial aid", "financial aid applicant", "applied for financial aid", "are you applying for financial aid", "applying for need based aid", "applying for aid", "seeking financial aid"], allow: "plan will intend need based" },
  { id: "fafsa_date", kind: "household", phrases: ["fafsa submission date", "date fafsa submitted", "date fafsa completed", "fafsa date", "fafsa completion date", "date you filed fafsa", "date fafsa filed", "fafsa submitted date", "date of fafsa submission", "when did you submit fafsa", "when did you file fafsa", "fafsa filing date"], allow: DATE_OK + " submitted completed filed" },
  { id: "fafsa_filed", kind: "household", phrases: ["fafsa", "completed fafsa", "submitted fafsa", "filed fafsa", "fafsa completed", "fafsa submitted", "fafsa filed", "have you completed fafsa", "have you submitted fafsa", "have you filed fafsa", "free application for federal student aid", "completed free application for federal student aid", "fafsa completion", "fafsa status"], weak: ["federal aid"], allow: "completed submitted filed have you yet" },
  { id: "css_profile", kind: "household", phrases: ["css profile", "css", "completed css profile", "submitted css profile", "css profile submitted", "css profile completed", "css financial aid profile", "have you completed css profile", "college board css profile"], allow: "completed submitted filed have you college board" },
  { id: "pell", kind: "household", phrases: ["pell grant eligible", "pell eligible", "eligible for pell grant", "pell grant eligibility", "eligible for federal pell grant", "federal pell grant eligible", "pell eligibility", "qualify for pell grant", "qualify for federal pell grant", "are you pell eligible", "are you eligible for pell grant"], weak: ["pell", "pell grant", "pell grant recipient", "receive pell grant", "pell recipient"], allow: "eligible eligibility qualify federal" },
  { id: "unmet_need", kind: "household", phrases: ["unmet need", "unmet financial need", "remaining need", "remaining financial need", "unmet cost", "financial gap", "remaining cost after aid", "unmet need after aid", "gap after financial aid", "amount of unmet need", "unmet need amount", "remaining balance after aid", "remaining cost of attendance"], allow: "after aid amount total approximate estimated estimate dollars" },
  { id: "free_lunch", kind: "household", phrases: ["free or reduced lunch", "free reduced lunch", "free and reduced lunch", "free or reduced price lunch", "eligible for free lunch", "free lunch", "reduced lunch", "frl", "national school lunch program", "free or reduced meals", "free reduced price lunch", "free or reduced price meals", "eligible for free or reduced lunch", "qualify for free or reduced lunch", "receive free or reduced lunch", "free and reduced price lunch"], allow: "eligible qualify receive receiving program price" },
  { id: "course_count", kind: "academic", phrases: ["number of ap courses", "number of ap classes", "many ap courses", "many ap classes", "number of honors courses", "number of honors classes", "many honors courses", "many honors classes", "number of ib courses", "many ib courses", "number of dual enrollment courses", "many dual enrollment courses", "number of dual enrollment classes", "number of college courses", "many college courses", "number of advanced courses", "many advanced courses", "number of ap ib courses", "number of ap and ib courses", "many ap ib courses", "number of ap ib classes", "total ap courses", "total number of ap courses", "number of courses", "number of classes", "many courses", "many classes", "total courses", "number of ap honors courses", "number of honors ap courses", "number of advanced placement courses", "many advanced placement courses", "number of dual credit courses", "many dual credit courses", "ap courses count", "ap classes count", "ap course count", "number of ap exams"], allow: "taken take have you completed currently enrolled including enrolled high school total" },
  { id: "course_names", kind: "academic", phrases: ["ap courses", "ap classes", "list ap courses", "list of ap courses", "ap courses taken", "ap classes taken", "honors courses", "honors classes", "ib courses", "ib classes", "dual enrollment courses", "dual enrollment classes", "dual credit courses", "college courses", "advanced courses", "advanced classes", "ap ib courses", "ap and ib courses", "ap honors courses", "honors ap courses", "advanced placement courses", "list ap ib courses", "list honors courses", "courses taken", "classes taken", "current courses", "current classes", "senior year courses", "senior courses", "senior schedule", "courses currently taking", "classes currently taking", "courses you are taking", "list courses", "list classes", "coursework", "relevant coursework", "rigorous courses", "honors ap ib courses", "ap ib honors courses", "list advanced courses"], allow: "taken take have you completed currently enrolled including list name names high school year all your rigorous" },
  { id: "exam_summary", kind: "academic", phrases: ["ap exam scores", "ap scores", "ap exams", "ap exams taken", "ib exam scores", "list ap exam scores", "ap test scores", "advanced placement exam scores", "ap exam results", "ap ib exam scores", "list ap exams and scores", "ap exams and scores", "ib exams", "ap and ib exam scores"], allow: "list taken your all scores results" },
  { id: "exam_score", kind: "list:exams", phrases: ["score", "exam score", "ap score", "ap exam score", "result"], allow: "" },
  { id: "exam_date", kind: "list:exams", phrases: ["date", "exam date", "test date", "date taken", "month taken", "year taken"], allow: DATE_OK },
  { id: "exam_name", kind: "list:exams", phrases: ["exam", "exam name", "ap exam", "exam subject", "subject", "test", "ap exam name", "ib exam"], allow: "name" },
  { id: "course_level", kind: "list:courses", phrases: ["course level", "level", "rigor", "course rigor", "level of course", "class level", "honors ap", "course designation", "designation", "course weight", "weighting"], allow: "" },
  { id: "course_grade_level", kind: "list:courses", phrases: ["grade level", "grade taken", "year taken", "grade level taken", "grade level when taken", "school year", "year", "grade in school", "grade completed"], allow: "" },
  { id: "course_term", kind: "list:courses", phrases: ["term", "semester", "term taken", "semester taken", "session"], allow: "" },
  { id: "course_grade", kind: "list:courses", phrases: ["grade", "final grade", "grade earned", "mark", "course grade", "grade received", "letter grade", "final mark", "grade achieved", "class grade"], allow: "final earned received" },
  { id: "course_credits", kind: "list:courses", phrases: ["credits", "credit", "credit hours", "units", "credits earned", "credit value", "course credits", "number of credits", "credit units"], allow: "earned" },
  { id: "course_points", kind: "list:courses", phrases: ["gpa points", "quality points", "grade points", "points", "weighted points", "gpa value", "grade point value", "course gpa"], allow: "weighted" },
  { id: "course_name", kind: "list:courses", phrases: ["course", "course name", "course title", "class name", "class title", "name of course", "name of class", "subject", "class", "course description"], allow: "name title full" },

  { id: "activity_hours", kind: "list:activities", phrases: ["hours per week", "hours week", "hrs week", "weekly hours", "hours per week spent", "average hours per week", "hrs per week", "hours wk", "hours", "hrs"], allow: "average approximate" },
  { id: "activity_weeks", kind: "list:activities", phrases: ["weeks per year", "weeks year", "weeks per year spent", "wks yr", "weeks", "number of weeks per year", "weeks per year involved"], allow: "average approximate" },
  { id: "activity_grades", kind: "list:activities", phrases: ["grades", "grade levels", "grades participated", "years participated", "grade level of participation", "grade", "grade levels participated", "participation grade levels", "years involved", "years of participation", "grade level"], allow: "participation participated" },
  { id: "activity_role", kind: "list:activities", phrases: ["role", "position", "position held", "leadership position", "title", "office held", "role position", "your role", "position title", "leadership role", "position or leadership", "positions held", "offices held", "role title", "leadership", "position leadership"], allow: "held" },
  { id: "activity_org", kind: "list:activities", phrases: ["organization", "organization name", "sponsoring organization", "group", "name of organization", "affiliated organization", "sponsor"], allow: "" },
  { id: "activity_description", kind: "list:activities", phrases: ["description", "describe", "activity description", "brief description", "description of activity", "describe your involvement", "details", "describe your role", "description of involvement", "description of your involvement", "responsibilities", "brief description of activity", "describe activity", "accomplishments"], allow: "brief short involvement role" },
  { id: "activity_name", kind: "list:activities", phrases: ["activity", "activity name", "name of activity", "activity title", "extracurricular activity", "extracurricular", "club", "club name", "organization or activity", "name of club", "activity or organization", "name of activity or organization", "activity organization"], allow: "name" },
  { id: "award_level", kind: "list:awards", phrases: ["level", "level of recognition", "recognition level", "award level", "level of award", "level of honor"], allow: "" },
  { id: "award_grade", kind: "list:awards", phrases: ["grade", "grade level", "year received", "date received", "grade received", "grade level received", "year awarded", "grade awarded", "when received"], allow: "" },
  { id: "award_name", kind: "list:awards", phrases: ["award", "award name", "honor", "honor or award", "name of award", "award honor", "name of honor", "award title", "honor title", "honor name", "award or honor", "title of award", "recognition", "honors and awards"], allow: "name title" },
  { id: "work_hours", kind: "list:work", phrases: ["hours per week", "hours week", "weekly hours", "hrs week", "average hours per week", "hours", "hrs"], allow: "average approximate" },
  { id: "work_start", kind: "list:work", phrases: ["start date", "date started", "start", "began", "beginning date", "employment start date", "from"], allow: DATE_OK },
  { id: "work_end", kind: "list:work", phrases: ["end date", "date ended", "end", "until", "ending date", "employment end date", "to"], allow: DATE_OK },
  { id: "work_description", kind: "list:work", phrases: ["description", "duties", "job description", "responsibilities", "describe", "job duties", "describe your job", "job responsibilities", "description of duties"], allow: "brief short" },
  { id: "work_title", kind: "list:work", phrases: ["job title", "position", "title", "position title", "role"], allow: "" },
  { id: "work_employer", kind: "list:work", phrases: ["employer", "company", "employer name", "business", "place of employment", "name of employer", "business name", "company name", "organization", "workplace"], allow: "name" },
];

const CHOOSE_GROUPS = [
  new Set(["act_composite", "sat_total", "act_english", "act_math", "act_reading", "act_science", "sat_erw", "sat_math"]),
  new Set(["email", "phone"]),
];

// Pre-tokenize phrases with the same normalization labels get.
for (const attr of ATTRS) {
  attr.seqs = attr.phrases.map(contentTokens).filter((s) => s.length);
  attr.shortSeqs = (attr.short || []).map(contentTokens);
  attr.weakSeqs = (attr.weak || []).map(contentTokens);
  // "current school" shrinks to "school" once filler words go; it must stay as weak as plain "school"
  attr.seqs = attr.seqs.filter((s) => !attr.weakSeqs.some((w) => w.join(" ") === s.join(" ")));
  attr.allowSet = words(attr.allow || "");
}
const ATTR_BY_ID = Object.fromEntries(ATTRS.map((a) => [a.id, a]));

function findSeq(toks, seq, from = 0) {
  outer: for (let i = from; i + seq.length <= toks.length; i++) {
    for (let j = 0; j < seq.length; j++) if (toks[i + j] !== seq[j]) continue outer;
    return i;
  }
  return -1;
}

// Best attribute for a token list: longest phrase wins, ties go to the earlier (more specific) attribute.
export function matchAttr(toks, { listOnly = null } = {}) {
  const found = [];
  const meaningful = toks.filter((t) => !isNumber(t));
  ATTRS.forEach((attr, order) => {
    if (listOnly !== null ? attr.kind !== `list:${listOnly}` : attr.kind.startsWith("list:")) return;
    const tries = [...attr.seqs.map((s) => [s, "strong"]), ...attr.weakSeqs.map((s) => [s, "weak"])];
    if (meaningful.length <= 2) tries.push(...attr.shortSeqs.map((s) => [s, "short"]));
    for (const [seq, strength] of tries) {
      let at = findSeq(toks, seq);
      while (at >= 0) {
        found.push({ attr, start: at, len: seq.length, strength, order, phrase: seq.join(" ") });
        at = findSeq(toks, seq, at + 1);
      }
    }
  });
  if (!found.length) return null;
  found.sort((a, b) => b.len - a.len || (a.strength === "weak") - (b.strength === "weak") || a.order - b.order || a.start - b.start);
  let best = found[0];
  // "High school city", "High school cumulative GPA": the school words say whose, the rest says what
  const SUBJECT_ONLY = words("high school secondary college university institution postsecondary");
  if (["school_name", "college_name"].includes(best.attr.id) && best.phrase.split(" ").every((t) => SUBJECT_ONLY.has(t))) {
    const other = found.find((f) => !["school_name", "college_name"].includes(f.attr.id) && (f.start >= best.start + best.len || f.start + f.len <= best.start));
    if (other) best = other;
  }
  const used = new Set();
  for (let i = best.start; i < best.start + best.len; i++) used.add(i);
  // A second, separate attribute of the same family ("ACT/SAT score", "phone or email") means the box is ambiguous.
  const rivals = [];
  for (const other of found.slice(1)) {
    if (other.attr.id === best.attr.id) continue;
    let overlaps = false;
    for (let i = other.start; i < other.start + other.len; i++) if (used.has(i)) overlaps = true;
    if (overlaps) continue;
    if (CHOOSE_GROUPS.some((g) => g.has(best.attr.id) && g.has(other.attr.id))) rivals.push(other.attr.id);
  }
  return { attr: best.attr, strength: best.strength, phrase: best.phrase, used, rivals: [...new Set(rivals)] };
}

// Who the tokens are about. Returns null when the text names nobody.
export function detectSubject(toks, skip = new Set()) {
  const live = toks.map((t, i) => (skip.has(i) ? null : t));
  const has = (set) => live.some((t) => t !== null && set.has(t));
  const idx = (set) => live.findIndex((t) => t !== null && set.has(t));
  const used = new Set();
  const mark = (set) => live.forEach((t, i) => { if (t !== null && set.has(t)) used.add(i); });
  if (has(SUBJECT.counselor)) { mark(SUBJECT.counselor); mark(SUBJECT.school); return { who: "counselor", used }; }
  if (has(SUBJECT.recommender)) {
    mark(SUBJECT.recommender); mark(SUBJECT.other);
    return { who: "recommender", index: listIndex(live, SUBJECT.recommender, used), used };
  }
  if (has(SUBJECT.parent)) {
    mark(SUBJECT.parent);
    let which = null;
    const rels = [];
    live.forEach((t) => { if (t !== null && PARENT_REL[t]) for (const r of PARENT_REL[t]) if (!rels.includes(r)) rels.push(r); });
    if (rels.length && live.some((t) => t === "guardian" || t === "guardians")) rels.push("Legal guardian");
    const start = idx(SUBJECT.parent);
    let end = start;
    while (end + 1 < live.length && live[end + 1] !== null && SUBJECT.parent.has(live[end + 1])) end++;
    for (const [n, set] of Object.entries(PARENT_INDEX_WORDS)) {
      const after = live[end + 1], before = live[start - 1];
      if (after != null && set.has(after) && !["first", "primary", "other"].includes(after)) { which = Number(n); used.add(end + 1); }
      else if (before != null && set.has(before) && !/^\d+$/.test(before)) { which = Number(n); used.add(start - 1); }
    }
    return { who: "parent", which, rels, generic: which === null && rels.length === 0, used };
  }
  if (has(SUBJECT.other)) return { who: "other", used };
  for (const [list, set] of Object.entries(LIST_WORDS)) {
    if (has(set)) { mark(set); return { who: "list", list, index: listIndex(live, set, used), used }; }
  }
  if (has(SUBJECT.college)) { mark(SUBJECT.college); return { who: "college", used }; }
  if (has(SUBJECT.school)) { mark(SUBJECT.school); live.forEach((t, i) => { if (t === "high" && live[i + 1] === "school") used.add(i); }); return { who: "school", used }; }
  if (has(SUBJECT.student)) { mark(SUBJECT.student); return { who: "student", used }; }
  return null;
}

// "Activity 2", "2nd activity", "Activity #2", "second reference" -> 1 (zero-based). null when not numbered.
function listIndex(live, nounSet, used) {
  for (let i = 0; i < live.length; i++) {
    if (live[i] === null || !nounSet.has(live[i])) continue;
    for (const j of [i + 1, i + 2]) {
      const t = live[j];
      if (t && /^\d{1,2}$/.test(t)) { used.add(j); return Number(t) - 1; }
      if (t && ORDINALS[t] && j === i + 1 && !["one"].includes(t)) { used.add(j); return ORDINALS[t] - 1; }
    }
    const before = live[i - 1];
    if (before && ORDINALS[before]) { used.add(i - 1); return ORDINALS[before] - 1; }
  }
  return null;
}

// ------------------------------------------------------------------ saved values

export function indexSchema(schema) {
  const items = {};
  for (const sec of schema.sections) for (const item of sec.items) items[item.key] = { ...item, section: sec.id, sectionTitle: sec.title };
  const lists = Object.fromEntries(schema.lists.map((l) => [l.id, l]));
  const states = Object.fromEntries(schema.states);
  return { items, lists, states, schema };
}

const join = (...parts) => parts.filter((p) => p && String(p).trim()).join(" ");
const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

function personParts(v, prefix) {
  return { first: v[`${prefix}first_name`], middle: v[`${prefix}middle_name`], last: v[`${prefix}last_name`] };
}

const DERIVED = {
  "student.full_name": { label: "Your full name (First Last)", type: "text", f: (v) => { const p = personParts(v, "student."); return p.first && p.last ? join(p.first, p.last) : null; } },
  "student.full_name_middle": { label: "Your full name with middle name", type: "text", f: (v) => { const p = personParts(v, "student."); return p.first && p.last && p.middle ? join(p.first, p.middle, p.last) : null; } },
  "student.last_first": { label: "Your name (Last, First)", type: "text", f: (v) => { const p = personParts(v, "student."); return p.first && p.last ? `${p.last}, ${p.first}` : null; } },
  "student.last_first_mi": { label: "Your name (Last, First M)", type: "text", f: (v) => { const p = personParts(v, "student."); return p.first && p.last && p.middle ? `${p.last}, ${p.first} ${p.middle[0]}` : null; } },
  "student.middle_initial": { label: "Your middle initial", type: "text", f: (v) => (v["student.middle_name"] ? v["student.middle_name"][0].toUpperCase() : null) },
  "student.address_full": { label: "Your full address (one line)", type: "text", f: (v) => {
    if (!(v["student.address_line1"] && v["student.city"] && v["student.state"] && v["student.zip"])) return null;
    return [v["student.address_line1"], v["student.address_line2"], v["student.city"]].filter(Boolean).join(", ") + `, ${v["student.state"]} ${v["student.zip"]}`;
  } },
  "student.city_state": { label: "Your city and state", type: "text", f: (v) => (v["student.city"] && v["student.state"] ? `${v["student.city"]}, ${v["student.state"]}` : null) },
  "student.city_state_zip": { label: "Your city, state and ZIP", type: "text", f: (v) => (v["student.city"] && v["student.state"] && v["student.zip"] ? `${v["student.city"]}, ${v["student.state"]} ${v["student.zip"]}` : null) },
  "student.us_citizen": { label: "U.S. citizen? (from your citizenship)", type: "yesno", f: (v) => (v["student.citizenship"] ? (v["student.citizenship"] === "U.S. citizen" ? "Yes" : "No") : null) },
  "student.citizenship_country": { label: "Country of citizenship", type: "text", f: (v) => (v["student.citizenship"] === "U.S. citizen" ? "United States" : null) },
  "school.city_state": { label: "School city and state", type: "text", f: (v) => (v["school.city"] && v["school.state"] ? `${v["school.city"]}, ${v["school.state"]}` : null) },
  "school.graduation_year": { label: "Graduation year", type: "year", f: (v) => (v["school.graduation"] ? v["school.graduation"].slice(0, 4) : null) },
  "school.graduation_month": { label: "Graduation month", type: "monthname", f: (v) => (v["school.graduation"] ? MONTH_NAMES[Number(v["school.graduation"].slice(5, 7)) - 1] : null) },
  "school.counselor_full_name": { label: "Counselor full name", type: "text", f: (v) => (v["school.counselor_first_name"] && v["school.counselor_last_name"] ? join(v["school.counselor_first_name"], v["school.counselor_last_name"]) : null) },
  "school.counselor_last_first": { label: "Counselor name (Last, First)", type: "text", f: (v) => (v["school.counselor_first_name"] && v["school.counselor_last_name"] ? `${v["school.counselor_last_name"]}, ${v["school.counselor_first_name"]}` : null) },
};
for (const n of [1, 2]) {
  DERIVED[`parent${n}.full_name`] = { label: `Parent ${n} full name`, type: "text", f: (v) => { const p = personParts(v, `parent${n}.`); return p.first && p.last ? join(p.first, p.last) : null; } };
  DERIVED[`parent${n}.last_first`] = { label: `Parent ${n} name (Last, First)`, type: "text", f: (v) => { const p = personParts(v, `parent${n}.`); return p.first && p.last ? `${p.last}, ${p.first}` : null; } };
}

// Look up any item reference: "student.email", "parent1.full_name" (derived), "activities.0.name", "custom.2".
export function itemInfo(ref, data, idx) {
  const values = data?.values || {};
  const lists = data?.lists || {};
  if (idx.items[ref]) {
    const item = idx.items[ref];
    return { ref, label: itemLabel(ref, idx), type: item.type, value: values[ref] ?? null, section: item.section, stored: true };
  }
  if (DERIVED[ref]) {
    const d = DERIVED[ref];
    return { ref, label: d.label, type: d.type, value: d.f(values) ?? null, section: ref.split(".")[0], derived: true };
  }
  const m = /^(\w+)\.(\d+)\.(\w+)$/.exec(ref);
  if (m && idx.lists[m[1]]) {
    const [, list, i, field] = m;
    const row = (lists[list] || [])[Number(i)] || null;
    const spec = idx.lists[list];
    const noun = `${spec.noun} ${Number(i) + 1}`;
    if (field === "full_name") {
      const v = row && row.first_name && row.last_name ? join(row.first_name, row.last_name) : null;
      return { ref, label: `${noun} full name`, type: "text", value: v, section: list, row: Number(i) };
    }
    const f = spec.fields.find((x) => x.id === field);
    if (!f) return null;
    const title = row && (row.name || row.employer || row.first_name || row.label);
    return { ref, label: `${noun}${title ? ` (${title})` : ""}: ${f.label}`, type: f.type, value: row ? row[field] ?? null : null, section: list, row: Number(i) };
  }
  if (ref === "exams.summary") {
    const rows = (lists.exams || []).filter((e) => e.exam);
    return { ref, label: "Your AP/IB exam scores", type: "text", section: "exams", derived: true,
      value: rows.length ? rows.map((e) => (e.score ? `${e.exam} (${e.score})` : e.exam)).join(", ") : null };
  }
  const cs = /^courses\.(count|names)\.([a-z+]+)\.(any|done|now)$/.exec(ref);
  if (cs) return courseSummary(ref, cs[1], cs[2].split("+"), cs[3], lists.courses || []);
  const c = /^custom\.(\d+)$/.exec(ref);
  if (c) {
    const row = (lists.custom || [])[Number(c[1])];
    if (!row) return null;
    return { ref, label: row.label, type: "text", value: row.value ?? null, section: "custom", custom: true };
  }
  return null;
}

// "Number of AP courses", "List your honors classes": worked out from your saved classes.
const COURSE_SETS = { all: null, ap: ["AP"], ib: ["IB"], honors: ["Honors"], de: ["Dual enrollment", "College course"],
  advanced: ["AP", "IB", "Honors", "Advanced", "Pre-AP", "Dual enrollment", "College course"] };
const COURSE_SET_WORDS = { all: "", ap: "AP", ib: "IB", honors: "honors", de: "dual enrollment", advanced: "advanced (AP, IB, honors, dual enrollment)" };
function courseSummary(ref, what, sets, status, rows) {
  const levels = sets.includes("all") ? null : new Set(sets.flatMap((k) => COURSE_SETS[k] || []));
  const wantStatus = status === "done" ? ["Completed"] : status === "now" ? ["In progress"] : ["Completed", "In progress"];
  const hits = rows.filter((r) => (!levels || levels.has(r.level)) && wantStatus.includes(r.status || "Completed"));
  const names = [...new Set(hits.map((r) => r.name).filter(Boolean))];
  const kindWord = sets.includes("all") ? "" : `${sets.map((k) => COURSE_SET_WORDS[k]).join(" and ")} `;
  const when = status === "done" ? " (completed)" : status === "now" ? " (in progress)" : "";
  const label = what === "count" ? `Number of your ${kindWord}classes${when}` : `Your ${kindWord}classes${when}`;
  const value = !rows.length ? null : what === "count" ? String(names.length) : names.length ? names.join(", ") : null;
  const inProgress = status === "any" ? hits.filter((r) => r.status === "In progress").length : 0;
  return { ref, label, type: what === "count" ? "integer" : "text", value, section: "courses", derived: true,
    note: inProgress ? `Includes ${inProgress} class${inProgress === 1 ? "" : "es"} still in progress` : null };
}

const SECTION_OWNER = { student: "You", contact: "You", parent1: "Parent 1", parent2: "Parent 2", school: "", academics: "", college: "", household: "", financial: "" };

export function itemLabel(ref, idx) {
  const item = idx.items[ref];
  if (!item) return DERIVED[ref]?.label || ref;
  const owner = SECTION_OWNER[item.section];
  if (item.section === "parent1" || item.section === "parent2") return `${owner} ${item.label.toLowerCase()}`;
  return item.label;
}

export function displayValue(info, states = {}) {
  if (!info || info.value == null) return "";
  const v = String(info.value);
  if (info.type === "phone" && /^\d{10}$/.test(v)) return `(${v.slice(0, 3)}) ${v.slice(3, 6)}-${v.slice(6)}`;
  if (info.type === "date" && /^\d{4}-\d{2}-\d{2}$/.test(v)) return `${v.slice(5, 7)}/${v.slice(8, 10)}/${v.slice(0, 4)}`;
  if (info.type === "month" && /^\d{4}-\d{2}$/.test(v)) return `${MONTH_NAMES[Number(v.slice(5, 7)) - 1]} ${v.slice(0, 4)}`;
  if (info.type === "state" && states[v]) return `${states[v]} (${v})`;
  return v;
}

// ------------------------------------------------------------------ subject + attribute -> item

function parentSlot(subject, data) {
  const v = data?.values || {};
  if (subject.which) return { slot: subject.which };
  if (subject.rels.length) {
    const hits = [1, 2].filter((n) => subject.rels.includes(v[`parent${n}.relationship`]));
    const exact = hits.filter((n) => v[`parent${n}.relationship`] === subject.rels[0]);
    if (exact.length === 1) return { slot: exact[0] };
    if (hits.length === 1) return { slot: hits[0], check: `Your parent ${hits[0]} is saved as ${v[`parent${hits[0]}.relationship`]}` };
    if (!v["parent1.relationship"] && !v["parent2.relationship"]) return { none: "Save each parent's relationship (Mother, Father…) in My info so I know who this is" };
    return { none: `No parent saved as ${subject.rels[0]}` };
  }
  return { slot: null };
}

const PERSON_ITEMS = {
  student: { first_name: "student.first_name", middle_name: "student.middle_name", middle_initial: "student.middle_initial", last_name: "student.last_name",
    full_name: "student.full_name", full_name_middle: "student.full_name_middle", last_first: "student.last_first", last_first_mi: "student.last_first_mi",
    preferred_name: "student.preferred_name", suffix: "student.suffix", email: "student.email", phone: "student.phone" },
  parent: { first_name: "first_name", last_name: "last_name", full_name: "full_name", last_first: "last_first", email: "email", phone: "phone",
    occupation: "occupation", title: "occupation", employer: "employer", education: "education", relationship: "relationship" },
  counselor: { first_name: "school.counselor_first_name", last_name: "school.counselor_last_name", full_name: "school.counselor_full_name",
    last_first: "school.counselor_last_first", email: "school.counselor_email", phone: "school.counselor_phone" },
  recommender: { first_name: "first_name", last_name: "last_name", full_name: "full_name", email: "email", phone: "phone", title: "title",
    occupation: "title", relationship: "relationship" },
};
const PLACE_ITEMS = {
  student: { address_full: "student.address_full", address_line1: "student.address_line1", address_line2: "student.address_line2", city: "student.city",
    state: "student.state", zip: "student.zip", county: "student.county", country: "student.country", city_state: "student.city_state", city_state_zip: "student.city_state_zip" },
  school: { address_line1: "school.address_line1", city: "school.city", state: "school.state", zip: "school.zip", city_state: "school.city_state" },
};
const OWN_ITEMS = {
  dob: "student.date_of_birth", gender: "student.gender", pronouns: "student.pronouns", citizenship: "student.citizenship", us_citizen: "student.us_citizen",
  citizenship_country: "student.citizenship_country", grade_level: "student.grade_level",
  ceeb: "school.ceeb", school_name: "school.name", graduation: "school.graduation", graduation_year: "school.graduation_year", graduation_month: "school.graduation_month",
  class_rank: "academics.class_rank", class_size: "academics.class_size", act_composite: "academics.act_composite", act_english: "academics.act_english",
  act_math: "academics.act_math", act_reading: "academics.act_reading", act_science: "academics.act_science", act_date: "academics.act_date",
  sat_total: "academics.sat_total", sat_erw: "academics.sat_erw", sat_math: "academics.sat_math", sat_date: "academics.sat_date",
  college_name: "college.name", major: "college.intended_major", career_goal: "college.career_goal", start_term: "college.start_term",
  degree: "college.degree", enrollment_status: "college.enrollment_status",
  household_size: "household.size", in_college: "household.in_college", marital_status: "household.parents_marital_status",
  first_generation: "household.first_generation", income: "financial.family_income", agi: "financial.agi",
  student_income: "financial.student_income", sai: "financial.sai", efc: "financial.sai", financial_need: "financial.financial_need",
  applying_for_aid: "financial.applying_for_aid", fafsa_filed: "financial.fafsa_filed", fafsa_date: "financial.fafsa_date",
  css_profile: "financial.css_profile", pell: "financial.pell_eligible", unmet_need: "financial.unmet_need", free_lunch: "financial.free_lunch",
};
const LIST_FIELD = {
  activity_name: "name", activity_role: "role", activity_org: "organization", activity_grades: "grades", activity_hours: "hours_per_week",
  activity_weeks: "weeks_per_year", activity_description: "description", award_name: "name", award_level: "level", award_grade: "grade",
  course_name: "name", course_level: "level", course_grade_level: "grade_level", course_term: "term", course_grade: "grade",
  course_credits: "credits", course_points: "gpa_points", exam_name: "exam", exam_score: "score", exam_date: "date",
  work_employer: "employer", work_title: "title", work_start: "start", work_end: "end", work_hours: "hours_per_week", work_description: "description",
};

// Subjects each kind of attribute tolerates. Anything else (a parent's GPA, a counselor's major) is not a match.
const KIND_SUBJECTS = {
  student: ["student"], school: ["student", "school"], academic: ["student", "school"], college: ["student", "college"],
  household: ["student", "parent"],
};

function resolve(attr, subject, field, ctx) {
  const v = ctx.data?.values || {};
  const who = subject?.who ?? "student";
  if (who === "other") return { none: "This box is about someone you haven't saved (a teacher, coach…)" };
  if (attr.id === "out_of") {
    const prev = normalize(field.prev || "");
    if (/\brank\b/.test(prev)) return { ref: "academics.class_size", check: "Assumed this is the class size after your rank" };
    if (/\bgpa\b|grade point/.test(prev)) return { gpaScale: true, check: "Assumed this is the GPA scale" };
    return { none: "Not sure what this is out of" };
  }
  if (attr.kind === "person") {
    if (who === "student" || who === "school" || who === "college") {
      if (who === "school" && attr.id === "full_name") return { ref: "school.name", check: "Assumed this is your school's name" };
      if (who === "college" && attr.id === "full_name") return { ref: "college.name", check: "Assumed this is your college's name" };
      if (who === "school" && attr.id === "phone") return { ref: "school.phone" };
      if (who !== "student") return { none: `This asks for the ${who}'s ${attr.id.replace(/_/g, " ")}` };
      const ref = PERSON_ITEMS.student[attr.id];
      return ref ? { ref } : { none: "You don't have this saved for yourself" };
    }
    if (who === "parent") {
      const field2 = PERSON_ITEMS.parent[attr.id];
      if (!field2) return { none: "That parent detail isn't something you save here" };
      const slot = parentSlot(subject, ctx.data);
      if (slot.none) return { none: slot.none };
      const n = slot.slot ?? ctx.genericIndex ?? 1;
      if (n > 2) return { none: "The form asks about more than two parents" };
      const ref = ["full_name", "last_first"].includes(field2) ? `parent${n}.${field2}` : `parent${n}.${field2}`;
      const both = Object.keys(v).some((k) => k.startsWith("parent2.")) && Object.keys(v).some((k) => k.startsWith("parent1."));
      const check = slot.check || (slot.slot == null && both ? `Assumed this is parent ${n} — check it's the right parent` : null);
      return { ref, check: check || (attr.id === "title" ? "Assumed 'title' means their job" : null) };
    }
    if (who === "counselor") {
      const ref = PERSON_ITEMS.counselor[attr.id];
      return ref ? { ref } : { none: "You don't save that for your counselor" };
    }
    if (who === "recommender") {
      const f = PERSON_ITEMS.recommender[attr.id];
      if (!f) return { none: "You don't save that for recommenders" };
      return { listRef: "recommenders", field: f, index: subject.index };
    }
    if (who === "list") return { none: "Not sure which saved item this is" };
  }
  if (attr.kind === "place") {
    if (who === "school") {
      const ref = PLACE_ITEMS.school[attr.id];
      return ref ? { ref } : { none: "You don't save that for your school" };
    }
    if (who === "student") {
      const ref = PLACE_ITEMS.student[attr.id];
      if (attr.id === "address_line1" && field.kind === "textarea") return { ref: "student.address_full", check: "This box looks like it wants the whole address" };
      if (attr.id === "address_line1" && ctx.features && !ctx.features.hasCity) return { ref: "student.address_full", check: "No separate city box, so I used your whole address" };
      return ref ? { ref } : { none: "Not saved" };
    }
    return { none: `This is the ${who}'s address, which you don't save here` };
  }
  if (attr.kind.startsWith("list:")) {
    const list = attr.kind.slice(5);
    return { listRef: list, field: LIST_FIELD[attr.id], index: subject?.index ?? null };
  }
  // the student's own details
  if (attr.needsParent && who !== "parent" && !ctx.phraseHasParent) return { none: "This may be asking about you, not your parents" };
  const allowed = KIND_SUBJECTS[attr.kind] || ["student"];
  if (subject && !allowed.includes(who)) return { none: `This asks about your ${who}, not you` };
  if (attr.id === "gpa") return { gpa: true };
  if (attr.id === "course_count" || attr.id === "course_names") return { courses: attr.id };
  if (attr.id === "exam_summary") return { ref: "exams.summary" };
  if (attr.id === "efc") return { ref: "financial.sai", check: "The box says EFC; the Student Aid Index (SAI) replaced it starting with the 2024-25 FAFSA" };
  if (attr.id === "gpa_scale") return { gpaScale: true };
  const ref = OWN_ITEMS[attr.id];
  if (!ref) return { none: "Not something you save here" };
  const check = attr.id === "school_name" && subject?.weak ? "Assumed 'school' means your high school" : null;
  return { ref, check };
}

// Which saved classes a "how many / list your … classes" box wants, from its words.
function pickCourses(field, toks, which) {
  const all = new Set([...toks, ...tokens(field.label)]); // "current" and "currently" are filler words elsewhere
  const has = (w) => all.has(w);
  const sets = [];
  if (has("ap") || (has("advanced") && has("placement"))) sets.push("ap");
  if (has("ib") || has("baccalaureate")) sets.push("ib");
  if (has("honors") || has("honor")) sets.push("honors");
  if (has("dual") || has("concurrent") || (has("college") && (has("courses") || has("classes") || has("credit")))) sets.push("de");
  let check = null;
  if (((has("advanced") && !has("placement")) || has("rigorous")) && !sets.length) {
    sets.push("advanced");
    check = "Counted AP, IB, honors, advanced and dual enrollment classes";
  }
  const status = has("currently") || has("current") || has("taking") || has("senior") || has("now") ? "now" : has("completed") ? "done" : "any";
  const count = which === "course_count" || field.type === "number" || (field.maxlength > 0 && field.maxlength <= 3);
  return { ref: `courses.${count ? "count" : "names"}.${sets.join("+") || "all"}.${status}`, check };
}

// Which GPA a box wants: the label's weighting words and any "4.0 scale" in it decide.
function pickGpa(field, toks, ctx, wantScale) {
  const v = ctx.data?.values || {};
  const raw = `${field.label || ""} ${(field.alt || []).join(" ")}`;
  const scaleMatch = /(\d{1,2}(?:\.\d{1,2})?)\s*(?:-?\s*point)?\s*(?:gpa\s*)?scale|out\s+of\s+(?:a\s+)?(\d{1,2}(?:\.\d{1,2})?)|scale\s+of\s+(\d{1,2}(?:\.\d{1,2})?)|(\d(?:\.\d{1,2})?)\s*\/\s*(\d(?:\.\d{1,2})?)/i.exec(raw);
  const askedScale = scaleMatch ? Number(scaleMatch[1] || scaleMatch[2] || scaleMatch[3] || scaleMatch[5]) : null;
  const unweighted = toks.includes("unweighted");
  const weighted = !unweighted && toks.includes("weighted");
  const have = {
    u: v["academics.gpa_unweighted"] != null ? { ref: wantScale ? "academics.gpa_unweighted_scale" : "academics.gpa_unweighted", scale: Number(v["academics.gpa_unweighted_scale"]) || null } : null,
    w: v["academics.gpa_weighted"] != null ? { ref: wantScale ? "academics.gpa_weighted_scale" : "academics.gpa_weighted", scale: Number(v["academics.gpa_weighted_scale"]) || null } : null,
  };
  const pick = (k, check) => {
    const h = have[k];
    if (!h) return { ref: wantScale ? `academics.gpa_${k === "u" ? "unweighted" : "weighted"}_scale` : `academics.gpa_${k === "u" ? "unweighted" : "weighted"}` };
    if (askedScale != null && !wantScale) {
      if (h.scale == null) return { ref: h.ref, check: `The box asks for a ${askedScale} scale; save your GPA scale to be sure` };
      if (Math.abs(h.scale - askedScale) > 1e-9) return { none: `The box wants a GPA on a ${askedScale} scale; yours is on ${h.scale}` };
    }
    return { ref: h.ref, check };
  };
  if (unweighted) return pick("u");
  if (weighted) return pick("w");
  if (askedScale != null && !wantScale) {
    const saved = ["u", "w"].filter((k) => have[k]);
    const fits = saved.filter((k) => have[k].scale != null && Math.abs(have[k].scale - askedScale) < 1e-9);
    if (fits.length === 1) return pick(fits[0]);
    if (saved.length && saved.every((k) => have[k].scale != null) && !fits.length)
      return { none: `The box wants a GPA on a ${askedScale} scale; yours ${saved.length > 1 ? "are" : "is"} on ${saved.map((k) => have[k].scale).join(" and ")}` };
  }
  if (have.u && have.w) return { choose: wantScale ? ["academics.gpa_unweighted_scale", "academics.gpa_weighted_scale"] : ["academics.gpa_unweighted", "academics.gpa_weighted"], why: "The box doesn't say weighted or unweighted" };
  if (have.w) return pick("w", "The box doesn't say weighted; you only saved a weighted GPA");
  return pick("u", have.u ? null : null);
}

// ------------------------------------------------------------------ blocked boxes

const BLOCKS = [
  [/\b(social security|ssn|ss|soc sec|itin|taxpayer id|tax id|tax identification)\b/, "Social Security or tax ID. You type this yourself."],
  [/\b(password|passcode|pass code|pin|security code|security question|cvv|cvc|card number|credit card|debit card|bank|routing|account number|acct number)\b/, "Password, PIN or bank details. You type this yourself."],
  [/\b(fsa id|fafsa id|student aid id|fsa username|fafsa pin)\b/, "FSA ID. You type this yourself."],
  [/\b(drivers license|driver license|licence number|license number|passport|alien registration|uscis|visa number|green card number)\b/, "ID document number. You type this yourself."],
  [/\b(signature|sign here|e signature|esignature|electronic signature|digital signature|signed|initials|initial here|sign your name|signature date)\b/, "Signature. You sign this yourself."],
  [/\b(captcha|not a robot|recaptcha|human verification)\b/, "Robot check."],
];
const AGREE = /\b(certify|certification|attest|attestation|agree|agreement|acknowledge|acknowledgement|consent|authorize|authorization|terms|conditions|waive|waiver|release|permission|accept|policy|privacy|understand|affirm|declare|swear)\b/;
const ESSAY = /\b(essay|personal statement|statement|describe|description|explain|tell us|why|how|discuss|reflect|share|prompt|response|words|characters|paragraph|elaborate|anything else|additional information|comments)\b/;

export function blockedReason(field) {
  const text = normalize([field.label, ...(field.alt || []), field.name, field.id, field.autocomplete].filter(Boolean).join(" "));
  if (field.kind === "secret") return "Password. You type this yourself.";
  if (field.kind === "file") return "Upload. You attach files yourself.";
  if (field.readonly) return "Read-only box.";
  for (const [re, why] of BLOCKS) if (re.test(text)) return why;
  if (["checkbox", "radio", "aria-checkbox", "aria-radio"].includes(field.kind) && AGREE.test(normalize(`${field.label} ${(field.options || []).map((o) => o.text).join(" ")}`)))
    return "Agreement or permission. That's your decision.";
  if (/\b(i agree|i certify|i attest)\b/.test(text)) return "Agreement. That's your decision.";
  return null;
}

// ------------------------------------------------------------------ typing a value into a specific box

function phoneVariants(d) {
  return { dash: `${d.slice(0, 3)}-${d.slice(3, 6)}-${d.slice(6)}`, paren: `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}`, plain: d, dot: `${d.slice(0, 3)}.${d.slice(3, 6)}.${d.slice(6)}` };
}

function hintText(field) {
  return `${field.placeholder || ""} ${field.pattern || ""} ${(field.alt || []).join(" ")} ${field.label || ""}`.toLowerCase();
}

function patternOk(field, value) {
  if (!field.pattern) return true;
  try { return new RegExp(`^(?:${field.pattern})$`, "v").test(value); } catch (e) {
    try { return new RegExp(`^(?:${field.pattern})$`, "u").test(value); } catch (e2) { return true; }
  }
}

function fitText(field, candidates) {
  // first candidate that satisfies the box's own pattern and length limits
  for (const c of candidates) {
    if (field.maxlength && c.length > field.maxlength) continue;
    if (!patternOk(field, c)) continue;
    return c;
  }
  return null;
}

function dateFormats(iso, hint, field) {
  const [y, m, d] = iso.split("-");
  const mm = m, dd = d, mi = String(Number(m)), di = String(Number(d)), yy = y.slice(2);
  const all = {
    "mm/dd/yyyy": `${mm}/${dd}/${y}`, "mm-dd-yyyy": `${mm}-${dd}-${y}`, "yyyy-mm-dd": iso, "dd/mm/yyyy": `${dd}/${mm}/${y}`,
    "mm/dd/yy": `${mm}/${dd}/${yy}`, "m/d/yyyy": `${mi}/${di}/${y}`, "mmddyyyy": `${mm}${dd}${y}`, "mm.dd.yyyy": `${mm}.${dd}.${y}`,
    "long": `${MONTH_NAMES[Number(m) - 1]} ${Number(d)}, ${y}`,
  };
  const h = hint.replace(/\s+/g, "");
  const order = [];
  if (/dd\/mm\/yyyy/.test(h)) order.push("dd/mm/yyyy");
  if (/mm\/dd\/yyyy|mm\/dd\/ccyy/.test(h)) order.push("mm/dd/yyyy");
  if (/mm-dd-yyyy/.test(h)) order.push("mm-dd-yyyy");
  if (/yyyy-mm-dd/.test(h)) order.push("yyyy-mm-dd");
  if (/mm\/dd\/yy(?!y)/.test(h)) order.push("mm/dd/yy");
  if (/m\/d\/yyyy/.test(h)) order.push("m/d/yyyy");
  if (/mmddyyyy/.test(h)) order.push("mmddyyyy");
  if (/mm\.dd\.yyyy/.test(h)) order.push("mm.dd.yyyy");
  const hinted = order.length > 0;
  if (!hinted) order.push("mm/dd/yyyy", "mm-dd-yyyy", "yyyy-mm-dd");
  return { candidates: order.map((k) => all[k]), hinted };
}

const PART_WORDS = { month: words("month mm mo"), day: words("day dd"), year: words("year yyyy yy yr") };

function datePart(field, toks) {
  const ac = (field.autocomplete || "").toLowerCase();
  if (/bday-month|cc-exp-month/.test(ac)) return "month";
  if (/bday-day/.test(ac)) return "day";
  if (/bday-year/.test(ac)) return "year";
  const ph = normalize(field.placeholder || "");
  if (/^(mm|month)$/.test(ph)) return "month";
  if (/^(dd|day)$/.test(ph)) return "day";
  if (/^(yyyy|yy|year)$/.test(ph)) return "year";
  const labelToks = toks.filter((t) => !STOP.has(t));
  for (const [part, set] of Object.entries(PART_WORDS)) if (labelToks.length && labelToks.every((t) => set.has(t))) return part;
  if (field.kind === "select" && field.options?.length) {
    const texts = field.options.map((o) => normalize(o.text)).filter(Boolean);
    const monthHits = texts.filter((t) => MONTH_NAMES.some((mn) => t === mn.toLowerCase() || t === mn.slice(0, 3).toLowerCase())).length;
    if (monthHits >= 12) return "month";
    const nums = texts.filter((t) => /^\d+$/.test(t)).map(Number);
    if (nums.length >= 28 && Math.max(...nums) === 31 && Math.min(...nums) <= 1) return "day";
    if (nums.length >= 5 && nums.every((n) => n >= 1900 && n <= 2100)) return "year";
  }
  return null;
}

function partCandidates(part, iso) {
  const [y, m, d] = iso.split("-");
  const mn = Number(m), dn = Number(d);
  if (part === "month") return [MONTH_NAMES[mn - 1], MONTH_NAMES[mn - 1].slice(0, 3), m, String(mn), `${m} ${MONTH_NAMES[mn - 1]}`, `${mn} ${MONTH_NAMES[mn - 1]}`, `${MONTH_NAMES[mn - 1]} ${m}`];
  if (part === "day") return d ? [d, String(dn), `${dn}${dn % 10 === 1 && dn !== 11 ? "st" : dn % 10 === 2 && dn !== 12 ? "nd" : dn % 10 === 3 && dn !== 13 ? "rd" : "th"}`] : [];
  return [y, y.slice(2)];
}

const SYNONYMS = {
  gender: { Male: ["male", "man", "m", "boy"], Female: ["female", "woman", "f", "girl"], "Non-binary": ["non binary", "nonbinary", "non binary genderqueer", "genderqueer non binary"], "Prefer not to say": ["prefer not to say", "prefer not to answer", "decline to state", "decline to answer", "i prefer not to say", "i prefer not to answer", "choose not to disclose"] },
  yesno: { Yes: ["yes", "y", "true"], No: ["no", "n", "false"] },
  citizenship: { "U.S. citizen": ["u s citizen", "us citizen", "united states citizen", "citizen", "citizen of the united states", "u s citizen or national", "us citizen or national"], "U.S. permanent resident": ["permanent resident", "u s permanent resident", "us permanent resident", "lawful permanent resident", "green card holder", "permanent resident green card holder", "permanent resident alien"], Other: [] },
  grade: { "9th grade": ["9", "9th", "9th grade", "grade 9", "freshman", "high school freshman", "ninth grade", "ninth"], "10th grade": ["10", "10th", "10th grade", "grade 10", "sophomore", "high school sophomore", "tenth grade", "tenth"], "11th grade": ["11", "11th", "11th grade", "grade 11", "junior", "high school junior", "eleventh grade", "eleventh"], "12th grade": ["12", "12th", "12th grade", "grade 12", "senior", "high school senior", "twelfth grade", "twelfth", "12th grade senior", "senior 12th grade"], "High school graduate": ["high school graduate", "graduated high school", "high school graduate gap year"], "College student": ["college student"] },
  relationship: { Mother: ["mother", "mom"], Father: ["father", "dad"], Stepmother: ["stepmother", "step mother"], Stepfather: ["stepfather", "step father"], "Legal guardian": ["legal guardian", "guardian"], Grandmother: ["grandmother"], Grandfather: ["grandfather"], Other: [] },
  education: { "Some high school": ["some high school", "less than high school", "did not complete high school", "did not finish high school", "some high school no diploma"], "High school diploma or GED": ["high school diploma or ged", "high school diploma ged", "high school graduate", "high school", "high school diploma", "high school graduate or ged", "high school ged", "ged", "high school or ged", "high school diploma or equivalent", "high school graduate or equivalent"], "Some college": ["some college", "some college no degree", "some college but no degree", "college but no degree"], "Associate degree": ["associate degree", "associates degree", "associate", "associates", "2 year degree", "two year degree", "associate s degree", "2 year college degree", "two year college degree"], "Bachelor's degree": ["bachelors degree", "bachelor degree", "bachelors", "bachelor", "4 year degree", "four year degree", "4 year college degree", "four year college degree", "college graduate 4 year"], "Master's degree": ["masters degree", "master degree", "masters", "master"], "Doctorate or professional degree": ["doctorate", "doctoral degree", "doctorate degree", "phd", "professional degree", "doctorate or professional degree", "doctoral or professional degree", "doctorate professional degree"], Unknown: ["unknown", "dont know", "do not know", "not sure", "i dont know"] },
  marital: { Married: ["married"], Remarried: ["remarried"], Divorced: ["divorced"], Separated: ["separated"], Widowed: ["widowed", "widow", "widower"], "Never married": ["never married", "single never married", "single"] },
  enrollment: { "Full-time": ["full time", "fulltime", "full"], "Part-time": ["part time", "parttime", "part"] },
};
SYNONYMS.course_level = { Regular: ["regular", "standard", "college prep", "cp", "general", "academic", "on level", "regular college prep", "standard level", "regular level"],
  Honors: ["honors", "honor", "h", "honors level"], Advanced: ["advanced", "accelerated", "adv", "gifted", "gt", "advanced level"],
  "Pre-AP": ["pre ap", "preap"], AP: ["ap", "advanced placement", "ap advanced placement", "advanced placement ap"],
  IB: ["ib", "international baccalaureate"], "Dual enrollment": ["dual enrollment", "dual credit", "de", "dual enrollment dual credit", "dual enrollment de", "concurrent enrollment", "college credit", "dual enrollment college credit", "dual credit dual enrollment"],
  "College course": ["college", "college course", "college level"], Other: [] };
SYNONYMS.course_status = { Completed: ["completed", "complete", "done", "finished"], "In progress": ["in progress", "current", "currently enrolled", "enrolled", "in process"], Planned: ["planned", "future", "scheduled"] };
const SYN_FOR = { "student.gender": "gender", "student.citizenship": "citizenship", "student.grade_level": "grade", "parent1.relationship": "relationship",
  "parent2.relationship": "relationship", "parent1.education": "education", "parent2.education": "education",
  "household.parents_marital_status": "marital", "college.enrollment_status": "enrollment" };

function optionCandidates(info, idx, part) {
  const v = String(info.value);
  if (part) return partCandidates(part, info.type === "month" ? `${v}-` : v).filter(Boolean);
  if (info.type === "state") return [v, idx.states[v], `${v} ${idx.states[v]}`, `${idx.states[v]} ${v}`].filter(Boolean);
  if (info.type === "yesno") return SYNONYMS.yesno[v] || [v];
  if (info.type === "monthname") { const i = MONTH_NAMES.indexOf(v) + 1; return [v, v.slice(0, 3), String(i).padStart(2, "0"), String(i)]; }
  const listSyn = /^courses\.\d+\.(level|grade_level|status)$/.exec(info.ref || "");
  const synKey = SYN_FOR[info.ref] || (listSyn && { level: "course_level", grade_level: "grade", status: "course_status" }[listSyn[1]]);
  const syn = synKey && SYNONYMS[synKey][v];
  if (syn) return [v, ...syn];
  if (info.type === "month") return [displayValue(info), v];
  return [v];
}

const PLACEHOLDER_OPTION = /^(select|choose|please select|please choose|pick|none selected|\s*)$/;

// The one option (by index) whose text or value equals a candidate. Never a "contains" match: "male" must not pick "Female".
// Returns the option index, null when nothing fits, or -1 when two different choices fit.
export function matchOption(options, candidates, { yesnoPrefix = false } = {}) {
  const want = new Set(candidates.map(normalize).filter(Boolean));
  let pool = [];
  options.forEach((o, i) => { if (!o.disabled && want.has(normalize(o.text))) pool.push(i); });
  if (!pool.length && yesnoPrefix) {
    // "Yes, I am a first-generation student" / "No, …" when exactly one option starts with the answer
    pool = options.map((o, i) => [normalize(o.text).split(" ")[0], i]).filter(([w, i]) => want.has(w) && !options[i].disabled).map(([, i]) => i);
    if (pool.length > 1) return -1;
  }
  if (!pool.length) return null;
  if (new Set(pool.map((i) => normalize(options[i].text))).size > 1) return -1;
  return pool[0];
}

// Dropdowns of ranges ("$50,000 - $74,999", "Under 25k", "6 or more"): the one range holding the number.
// Returns the index, null when none holds it, or -1 when two do (a shared boundary).
function parseAmount(text) {
  const m = /^\s*(-?\d+(?:\.\d+)?)\s*(k|m|thousand|million)?\b/.exec(text);
  if (!m) return null;
  const unit = (m[2] || "").toLowerCase();
  return Number(m[1]) * (unit === "k" || unit === "thousand" ? 1e3 : unit === "m" || unit === "million" ? 1e6 : 1);
}
export function parseRange(text) {
  const t = String(text).toLowerCase().replace(/[$,]/g, "").replace(/\s+/g, " ").trim();
  let m;
  if ((m = /^(?:less than|under|below|fewer than|<)\s*(.+)$/.exec(t))) { const n = parseAmount(m[1]); return n == null ? null : { hi: n, hiOpen: true }; }
  if ((m = /^(?:up to|no more than|at most|<=|≤)\s*(.+)$/.exec(t))) { const n = parseAmount(m[1]); return n == null ? null : { hi: n }; }
  if ((m = /^(?:over|above|more than|greater than|>)\s*(.+)$/.exec(t))) { const n = parseAmount(m[1]); return n == null ? null : { lo: n, loOpen: true }; }
  if ((m = /^(?:at least|>=|≥)\s*(.+)$/.exec(t))) { const n = parseAmount(m[1]); return n == null ? null : { lo: n }; }
  if ((m = /^(-?\d+(?:\.\d+)?\s*(?:k|m|thousand|million)?)\s*(?:\+|or more|or above|or higher|or greater|and above|and over|and up|and higher|or over)(?:\s|$)/.exec(t))) return { lo: parseAmount(m[1]) };
  if ((m = /^(-?\d+(?:\.\d+)?\s*(?:k|m|thousand|million)?)\s*(?:or less|or fewer|or under|and under|and below|or below)(?:\s|$)/.exec(t))) return { hi: parseAmount(m[1]) };
  if ((m = /^(-?\d+(?:\.\d+)?\s*(?:k|m|thousand|million)?)\s*(?:-|–|—|to|through|thru)\s*(-?\d+(?:\.\d+)?\s*(?:k|m|thousand|million)?)(?:\s|$)/.exec(t))) {
    let lo = parseAmount(m[1]), hi = parseAmount(m[2]);
    if (!/[km]|thousand|million/.test(m[1]) && /k|thousand/.test(m[2])) lo *= 1e3; // "50-75k"
    return lo != null && hi != null && lo <= hi ? { lo, hi } : null;
  }
  return null;
}
function rangeOption(options, num) {
  if (!Number.isFinite(num)) return null;
  const hits = [];
  options.forEach((o, i) => {
    if (o.disabled) return;
    const r = parseRange(o.text);
    if (!r) return;
    const okLo = r.lo == null || (r.loOpen ? num > r.lo : num >= r.lo);
    const okHi = r.hi == null || (r.hiOpen ? num < r.hi : num <= r.hi);
    if (okLo && okHi) hits.push(i);
  });
  return hits.length === 1 ? hits[0] : hits.length > 1 ? -1 : null;
}

// How to put info into this box. Returns {action, display, note} or {fail}.
export function formatFor(field, info, idx, { part = null } = {}) {
  if (info.value == null || info.value === "") return { missing: true };
  const v = String(info.value);
  const kind = field.kind;
  if (kind === "select" || kind === "radio" || kind === "aria-radio" || (kind === "combo" && field.options?.length)) {
    const opts = field.options;
    if (!opts?.length) return { fail: "This box has no choices to pick from" };
    if (info.type === "date" && !part) return { fail: "This dropdown needs part of a date; pick it yourself" };
    let i = info.type === "month" && !part
      ? matchOption(opts, [displayValue(info), `${v.slice(5, 7)}/${v.slice(0, 4)}`])
      : matchOption(opts, optionCandidates(info, idx, part), { yesnoPrefix: info.type === "yesno" });
    let viaRange = false;
    if (i == null && ["number", "integer"].includes(info.type)) { i = rangeOption(opts, Number(v)); viaRange = i != null && i >= 0; }
    const act = (n) => (kind === "select" ? { kind: "select", index: n } : kind === "combo" ? { kind: "combo", text: opts[n].text } : { kind: "radio", option: n });
    if (viaRange) return { action: act(i), display: opts[i].text, note: `${Number(v).toLocaleString("en-US")} falls in this range` };
    if (i === -1) return { fail: `More than one choice could mean "${displayValue(info, idx.states)}". Pick it yourself.`, copy: displayValue(info, idx.states) };
    if (i == null) return { fail: `None of the choices matches "${displayValue(info, idx.states)}"`, copy: displayValue(info, idx.states) };
    return { action: act(i), display: opts[i].text };
  }
  if (kind === "checkbox" || kind === "aria-checkbox") {
    if (info.type !== "yesno") return { fail: "Checkboxes only take yes/no answers" };
    if (v === "Yes") return { action: { kind: "check", checked: true }, display: "☑ checked" };
    return { nothing: "Your answer is No, so this stays unchecked" };
  }
  if (kind === "combo") {
    // a dropdown built from divs: its choices only appear once it's opened, which happens when you press Fill
    if (info.type === "date" && !part) return { fail: "This dropdown needs part of a date; pick it yourself", copy: displayValue(info, idx.states) };
    if (info.type === "longtext") return { fail: "Too long for a dropdown", copy: v };
    return { action: { kind: "combo" }, display: displayValue(info, idx.states), note: "A custom dropdown: I'll open it when you press Fill and pick only an exact match" };
  }
  if (kind === "widget") return { fail: "This is a custom box I can't type into safely. Copy and paste it.", copy: displayValue(info, idx.states) };
  // text-like boxes
  const ftype = field.type || "text";
  const hint = hintText(field);
  let candidates, note = null;
  if (part && (info.type === "date" || info.type === "month")) {
    const [y, m, d] = v.split("-");
    if (part === "month") candidates = /mm/.test(hint) || field.maxlength === 2 ? [m] : [m, MONTH_NAMES[Number(m) - 1]];
    else if (part === "day") candidates = d ? [d, String(Number(d))] : [];
    else candidates = field.maxlength === 2 ? [y.slice(2)] : [y];
    if (!candidates.length) return { fail: "You saved a month and year, not a day" };
  } else if (info.type === "phone") {
    const p = phoneVariants(v);
    if (/\(\s*\d{3}\s*\)|\(xxx\)|\(___\)|\(\s*\)/.test(hint) || field.maxlength === 14) candidates = [p.paren, p.dash, p.plain];
    else if (field.maxlength === 10 || /^\\d\{10\}$|^\[0-9\]\{10\}$/.test(field.pattern || "")) candidates = [p.plain];
    else if (/\d{3}\.\d{3}/.test(hint)) candidates = [p.dot, p.dash];
    else candidates = [p.dash, p.paren, p.plain];
  } else if (info.type === "date") {
    if (ftype === "date") candidates = [v];
    else if (ftype === "month") return { fail: "This box wants a month, but this is a full date" };
    else {
      const f = dateFormats(v, hint, field);
      candidates = f.candidates;
      if (!f.hinted) note = `Typed as ${candidates[0]} (month/day/year). Check the box accepts it.`;
    }
  } else if (info.type === "month") {
    const mm = v.slice(5, 7), y = v.slice(0, 4);
    if (ftype === "month") candidates = [v];
    else if (ftype === "date") return { fail: "This box wants an exact day; you saved a month and year" };
    else if (/mm\s*\/\s*yyyy|mm\/yyyy/.test(hint.replace(/\s/g, ""))) candidates = [`${mm}/${y}`];
    else if (/mm\s*\/\s*yy\b/.test(hint)) candidates = [`${mm}/${y.slice(2)}`];
    else { candidates = [displayValue(info), `${mm}/${y}`]; note = `Typed as "${displayValue(info)}". Check the box accepts it.`; }
  } else if (info.type === "state") {
    const name = idx.states[v];
    const wantsName = field.maxlength !== 2 && name && field.placeholder && normalize(field.placeholder).length > 3 && Object.values(idx.states).some((n) => normalize(field.placeholder).includes(normalize(n)));
    candidates = wantsName ? [name, v] : [v, name];
  } else if (info.type === "number" || info.type === "integer") {
    candidates = [v];
    if (ftype === "number") {
      const num = Number(v);
      if (field.min !== "" && field.min != null && num < Number(field.min)) return { fail: `The box only allows ${field.min} or more` };
      if (field.max !== "" && field.max != null && num > Number(field.max)) return { fail: `The box only allows up to ${field.max}` };
      const step = field.step === "any" ? null : Number(field.step || 1);
      if (step && Math.abs(Math.round(num / step) * step - num) > 1e-9) return { fail: step === 1 ? "This box only takes whole numbers" : `This box takes steps of ${field.step}` };
    }
  } else {
    candidates = [v];
  }
  if (ftype === "email" && info.type !== "email") return { fail: "This box wants an email address" };
  if (ftype === "tel" && info.type !== "phone") return { fail: "This box wants a phone number" };
  if (ftype === "number" && !["number", "integer", "year"].includes(info.type) && !(part && /^\d+$/.test(candidates[0] || ""))) return { fail: "This box only takes numbers" };
  if (ftype === "date" && info.type !== "date") return { fail: "This box wants a date" };
  if (kind === "text" && info.type === "longtext" && v.includes("\n")) candidates = [v.replace(/\s*\n+\s*/g, " ")];
  const value = fitText(field, candidates);
  if (value == null) {
    if (field.maxlength && candidates.every((c) => c.length > field.maxlength)) return { fail: `Too long for this box (it allows ${field.maxlength} characters)` };
    return { fail: "Doesn't match the format this box requires" };
  }
  return { action: { kind: "text", value }, display: value, note };
}

// ------------------------------------------------------------------ one box

function sameValue(field, action) {
  if (!action) return false;
  if (action.kind === "text") return normalize(field.value) === normalize(action.value) && String(field.value).trim() !== "";
  if (action.kind === "select") return field.selectedIndex === action.index && !field.selectDefault;
  if (action.kind === "radio") return field.options?.[action.option]?.checked === true;
  if (action.kind === "check") return field.checked === action.checked;
  return false;
}

function hasValue(field) {
  if (field.kind === "select") {
    const o = field.options?.[field.selectedIndex];
    if (!o || field.selectDefault || o.value === "") return false;
    return !PLACEHOLDER_OPTION.test(normalize(o.text)) && !/^\s*(-|select|choose|please)/i.test(o.text) && normalize(o.text) !== normalize(field.label);
  }
  if (field.kind === "radio" || field.kind === "aria-radio") return (field.options || []).some((o) => o.checked);
  if (field.kind === "checkbox" || field.kind === "aria-checkbox") return false;
  return String(field.value ?? "").trim() !== "";
}

function currentText(field) {
  if (field.kind === "select") return field.options?.[field.selectedIndex]?.text || "";
  if (field.kind === "radio" || field.kind === "aria-radio") return (field.options || []).find((o) => o.checked)?.text || "";
  return String(field.value ?? "");
}

// Build the row for a box once we know which saved item it gets.
function finishRow(row, field, info, idx, { part = null, check = null } = {}) {
  row.item = info.ref;
  row.part = part;
  row.itemLabel = info.label;
  const out = formatFor(field, info, idx, { part });
  if (out.missing) return Object.assign(row, { status: "missing", reason: `${info.label} isn't saved yet` });
  if (out.nothing) return Object.assign(row, { status: "done", reason: out.nothing });
  if (out.fail) return Object.assign(row, { status: "none", reason: out.fail, copy: out.copy || null });
  row.action = out.action;
  row.display = out.display;
  if (sameValue(field, out.action)) return Object.assign(row, { status: "done", reason: "Already filled in" });
  if (hasValue(field)) return Object.assign(row, { status: "has_value", current: currentText(field), reason: `Has "${currentText(field).slice(0, 40)}" already. Tick to replace it.` });
  const notes = [check, out.note].filter(Boolean);
  if (notes.length) return Object.assign(row, { status: "check", reason: notes.join(" · ") });
  return Object.assign(row, { status: "ready", reason: `Matches ${info.label}` });
}

export function analyzeField(field, ctx) {
  const { idx, data } = ctx;
  const row = { ref: field.ref, label: field.label || field.alt?.[0] || field.name || "(no label)", status: "none" };
  const blocked = blockedReason(field);
  if (blocked) return Object.assign(row, { status: "blocked", reason: blocked });
  if (field.disabled) return Object.assign(row, { status: "blocked", reason: "Box is switched off" });

  // 1. the user's own saved answers, matched by their exact question text
  const label = normalize(field.label);
  const customs = data?.lists?.custom || [];
  for (let i = 0; i < customs.length; i++) {
    const names = [customs[i].label, ...String(customs[i].also || "").split(",")].map(normalize).filter(Boolean);
    if (label && names.includes(label)) return finishRow(row, field, itemInfo(`custom.${i}`, data, idx), idx);
  }

  // 2. what the box asks for
  const contexts = ctx.contexts || [];
  const labelToks = contentTokens(field.label || "");
  let toks = labelToks, source = "label", m = null, subject = null, subjectFrom = null, subjectFromIndex = null, conflict = null;
  // rows of a saved list ("Activity 2 – hours per week"), named by the label or by the nearest heading
  const labelSubject = detectSubject(labelToks);
  const summaryAsk = ["exam_summary", "course_names", "course_count"].includes(matchAttr(labelToks)?.attr.id); // "List your AP exam scores"
  let listSubject = labelSubject?.who === "list" && !labelToks.some((t) => COUNT_WORDS.has(t)) && !summaryAsk ? labelSubject : null;
  if (!listSubject && !labelSubject) {
    for (const c of contexts) {
      const s = detectSubject(contentTokens(c));
      if (s) { if (s.who === "list") { listSubject = s; subjectFrom = c; } break; }
    }
  }
  if (listSubject) {
    m = matchAttr(labelToks, { listOnly: listSubject.list }) || (labelToks.length === 1 && labelToks[0] === "name" && ["activities", "awards"].includes(listSubject.list)
      ? { attr: ATTR_BY_ID[listSubject.list === "activities" ? "activity_name" : "award_name"], strength: "strong", used: new Set([0]), rivals: [] } : null);
    if (m) subject = listSubject;
    else subjectFrom = null;
  }
  if (!m) m = matchAttr(labelToks);
  if (!m && field.autocomplete) {
    const ac = AC_ATTR[(field.autocomplete || "").toLowerCase().split(/\s+/).pop()];
    if (ac) { m = { attr: ATTR_BY_ID[ac], strength: "autocomplete", used: new Set(), rivals: [] }; source = "autocomplete"; }
  }
  if (!m && !labelToks.length) {
    for (const alt of field.alt || []) {
      const altToks = contentTokens(alt);
      const am = matchAttr(altToks);
      if (am) { m = am; toks = altToks; source = "hint"; break; }
    }
  }
  // date parts: a box labelled only "Month" under "Date of birth"
  let part = datePart(field, labelToks);
  if (part && (!m || PART_ONLY(labelToks))) {
    m = null;
    for (const c of contexts) {
      const ct = contentTokens(c);
      const cm = matchAttr(ct);
      if (cm && ["dob", "graduation", "act_date", "sat_date"].includes(cm.attr.id)) { m = cm; toks = ct; source = "context"; break; }
    }
    if (!m) return Object.assign(row, { status: "none", reason: `This is the ${part} of some date; I can't tell which` });
  }
  if (!m) {
    if (field.kind === "textarea") return Object.assign(row, { status: "blocked", reason: "Long answer or essay. You write this." });
    return Object.assign(row, { status: "none", reason: "I don't know what this box wants" });
  }
  if (m.rivals.length) {
    const refs = [m.attr.id, ...m.rivals].map((a) => OWN_ITEMS[a] || PERSON_ITEMS.student[a]).filter(Boolean);
    return Object.assign(row, { status: "choose", choices: refs, reason: "The box asks for one of several things. Choose which." });
  }
  if (field.kind === "textarea" && !/description/.test(m.attr.id) && !["address_line1", "address_full", "course_names", "exam_summary"].includes(m.attr.id))
    return Object.assign(row, { status: "blocked", reason: "Long answer or essay. You write this." });
  if (attrType(m.attr.id) === "other") part = null;

  // 3. who it's about: the label first, then the box's group and headings, nearest first
  if (!subject) {
    subject = detectSubject(toks, m.used);
    subjectFrom = subject ? source : null;
    const personish = ["person", "place", "household"].includes(m.attr.kind);
    if (personish && (!subject || subject.who === "student")) {
      for (const [ci, c] of contexts.entries()) {
        const s = detectSubject(contentTokens(c));
        if (!s) continue;
        if (s.who === "student" || s.who === "list") break;
        if (!subject) { subject = s; subjectFrom = c; subjectFromIndex = ci; }
        else if (["parent", "counselor", "recommender"].includes(s.who) && !labelSaysStudent(toks, m.used)) conflict = { other: s, heading: c };
        break;
      }
    }
    if (!subject && field.nameHint) {
      const s = detectSubject(contentTokens(field.nameHint));
      if (s && !["student", "list"].includes(s.who)) { subject = s; subjectFrom = "name"; }
    }
  }
  if (subject?.who === "school" && m.attr.id === "school_name") subject = null;
  if (subject?.who === "parent" && subject.generic && ctx.genericIndex) subject = { ...subject, genericIndex: ctx.genericIndex };

  // 4. leftover words that change the meaning
  const spent = new Set([...m.used, ...(subjectFrom === source && subject?.used ? subject.used : [])]);
  const leftovers = toks.filter((t, i) => !spent.has(i) && !isNumber(t) && !m.attr.allowSet.has(t) && !isSubjectWord(t, m.attr));
  const risky = leftovers.filter((t) => RISKY.has(t));
  if (risky.length) return Object.assign(row, { status: "none", reason: `The box says "${risky[0]}", so it may want something else` });
  const explain = leftovers.find((t) => EXPLAIN.has(t));
  if (explain) return Object.assign(row, { status: "none", reason: `The box asks you to ${explain} something. You write that.` });
  if (m.attr.id === "full_name" && m.phrase === "name" && leftovers.length)
    return Object.assign(row, { status: "none", reason: `Not sure whose name "${field.label}" means` });
  if ((m.attr.kind === "person" || m.attr.kind === "place") && !["parent", "recommender"].includes(subject?.who) && m.used.size) {
    const last = Math.max(...m.used);
    if (isNumber(toks[last + 1] || "") && Number(toks[last + 1]) >= 2 && m.attr.id !== "address_line2")
      return Object.assign(row, { status: "none", reason: "This looks like a second or extra box" });
  }

  // 5. the saved item
  const phraseHasParent = [...m.used].some((i) => SUBJECT.parent.has(toks[i]));
  if (conflict) {
    const mine = resolve(m.attr, subject, field, ctx), theirs = resolve(m.attr, conflict.other, field, ctx);
    if (mine.ref && theirs.ref && mine.ref !== theirs.ref)
      return Object.assign(row, { status: "choose", choices: [mine.ref, theirs.ref], reason: `The box says "your" but it's under "${conflict.heading}". Choose whose it is.` });
  }
  const r = resolve(m.attr, subject, field, { ...ctx, genericIndex: subject?.genericIndex, phraseHasParent });
  const checks = [];
  if (leftovers.length) checks.push(`The box also says "${leftovers.slice(0, 3).join(" ")}"`);
  if (source === "hint") checks.push("Read from the box's hint text, not a label");
  if (source === "autocomplete") checks.push("The page marks this box for this, but it has no label");
  if (m.strength === "weak") checks.push(`The box only says "${field.label}"`);
  if (subjectFrom === "name") checks.push("Who it's for comes from the page's code, not the label");
  if (r.none) return Object.assign(row, { status: "none", reason: r.none });
  if (r.check) checks.push(r.check);
  if (r.choose) return Object.assign(row, { status: "choose", choices: r.choose, reason: r.why });
  let ref = r.ref;
  if (r.gpa || r.gpaScale) {
    const g = pickGpa(field, toks, ctx, !!r.gpaScale);
    if (g.none) return Object.assign(row, { status: "none", reason: g.none });
    if (g.choose) return Object.assign(row, { status: "choose", choices: g.choose, reason: g.why });
    if (g.check) checks.push(g.check);
    ref = g.ref;
  }
  if (r.courses) {
    const c = pickCourses(field, toks, r.courses);
    if (c.check) checks.push(c.check);
    ref = c.ref;
  }
  if (r.listRef) {
    const rows = data?.lists?.[r.listRef] || [];
    let i = r.index;
    if (i == null && ctx.listOrder) i = ctx.listOrder(r.listRef, m.attr.id, field.ref);
    if (i == null) return Object.assign(row, { status: "none", reason: "Not sure which saved row this is. Choose it." });
    if (r.index == null) checks.push(`Assumed this is ${idx.lists[r.listRef].noun.toLowerCase()} ${i + 1} from the order on the page`);
    if (!rows[i]) return Object.assign(row, { status: "missing", item: `${r.listRef}.${i}.${r.field}`, itemLabel: `${idx.lists[r.listRef].noun} ${i + 1}`, reason: `You have ${rows.length} saved ${idx.lists[r.listRef].title.toLowerCase()}` });
    ref = `${r.listRef}.${i}.${r.field}`;
  }
  const info = itemInfo(ref, data, idx);
  if (!info) return Object.assign(row, { status: "none", reason: "Not something you save here" });
  if (info.note) checks.push(info.note);
  row.meta = { attr: m.attr.id, who: subject?.who || "student", generic: !!subject?.generic, from: subjectFrom === source ? "label" : subjectFrom,
    fromId: subjectFromIndex == null ? null : ctx.contextIds?.[subjectFromIndex] ?? null };
  return finishRow(row, field, info, idx, { part, check: checks.join(" · ") || null });
}

// "Your mother's email" is about the parent; "Your email" under a parent heading is still yours.
function labelSaysStudent(toks, used) {
  return toks.some((t, i) => !used.has(i) && ["student", "students", "applicant", "applicants", "yourself", "nominee"].includes(t));
}

const PART_ONLY = (toks) => toks.length > 0 && toks.every((t) => PART_WORDS.month.has(t) || PART_WORDS.day.has(t) || PART_WORDS.year.has(t));

function isSubjectWord(t, attr) {
  if (attr.kind.startsWith("list:")) return LIST_WORDS[attr.kind.slice(5)].has(t) || SUBJECT.student.has(t);
  if (SUBJECT.student.has(t) || SUBJECT.parent.has(t) || SUBJECT.counselor.has(t) || SUBJECT.recommender.has(t)) return true;
  if (attr.kind === "college") return SUBJECT.college.has(t);
  return SUBJECT.school.has(t) || t === "high";
}

function attrType(id) {
  if (["dob", "act_date", "sat_date"].includes(id)) return "date";
  if (id === "graduation") return "month";
  return "other";
}

const AC_ATTR = { "given-name": "first_name", "additional-name": "middle_name", "family-name": "last_name", name: "full_name",
  "honorific-suffix": "suffix", nickname: "preferred_name", email: "email", tel: "phone", "tel-national": "phone",
  "address-line1": "address_line1", "address-line2": "address_line2", "street-address": "address_line1", "address-level2": "city",
  "address-level1": "state", "postal-code": "zip", "country-name": "country", bday: "dob", sex: "gender" };

// ------------------------------------------------------------------ a whole page (or a circled part of it)

export function proposeAll(fields, data, schema, { only = null, items = null } = {}) {
  const idx = schema.items ? schema : indexSchema(schema);
  const features = { hasCity: fields.some((f) => /\b(city|town)\b/.test(normalize(f.label)) || /address-level2/.test(f.autocomplete || "")) };
  const order = new Map(fields.map((f, i) => [f.ref, i]));
  const listSeen = new Map();
  const listOrder = (list, attrId, ref) => {
    const key = `${list}|${attrId}`;
    if (!listSeen.has(key)) listSeen.set(key, []);
    const seen = listSeen.get(key);
    if (!seen.includes(ref)) seen.push(ref);
    return seen.indexOf(ref);
  };
  const ctxFor = (field, extra = {}) => {
    const pairs = [[field.group, field.groupId], ...(field.contexts || []).map((c, i) => [c, field.contextIds?.[i]])].filter(([c]) => c);
    return { idx, data, features, listOrder, contexts: pairs.map((x) => x[0]), contextIds: pairs.map((x) => x[1] ?? null), ...extra };
  };
  const rows = fields.map((field) => analyzeField(field, ctxFor(field)));
  // Unnumbered "Parent/Guardian" boxes: separate parent sections, or repeated labels, are parents 1 and 2 in page order.
  const sections = [], seenLabels = new Map();
  rows.forEach((row, i) => {
    if (!row.meta?.generic || row.meta.who !== "parent") return;
    const field = fields[i];
    let n;
    if (row.meta.from && row.meta.from !== "label") {
      const key = row.meta.fromId ?? normalize(row.meta.from);
      if (!sections.includes(key)) sections.push(key);
      n = sections.indexOf(key) + 1;
    } else {
      const key = `${row.meta.attr}|${normalize(field.label)}`;
      seenLabels.set(key, (seenLabels.get(key) || 0) + 1);
      n = seenLabels.get(key);
    }
    if (n > 1) rows[i] = analyzeField(field, ctxFor(field, { genericIndex: n }));
  });
  let out = rows.filter((r) => !only || only.has(r.ref));
  if (items) out = out.filter((r) => (r.item && items.has(r.item)) || (r.choices || []).some((c) => items.has(c)));
  return out.sort((a, b) => order.get(a.ref) - order.get(b.ref));
}

// Re-run one box with an item the user chose ("put my counselor's email here").
export function proposeForItem(field, ref, data, schema) {
  const idx = schema.items ? schema : indexSchema(schema);
  const row = { ref: field.ref, label: field.label || field.alt?.[0] || field.name || "(no label)", status: "none", chosen: true };
  const blocked = blockedReason(field);
  if (blocked) return Object.assign(row, { status: "blocked", reason: blocked });
  if (field.disabled) return Object.assign(row, { status: "blocked", reason: "Box is switched off" });
  const info = itemInfo(ref, data, idx);
  if (!info) return Object.assign(row, { status: "none", reason: "Unknown item" });
  const part = ["date", "month"].includes(info.type) ? datePart(field, contentTokens(field.label || "")) : null;
  const out = finishRow(row, field, info, idx, { part });
  if (out.status === "check") out.status = "ready"; // the user picked both the box and the answer
  return out;
}

// ------------------------------------------------------------------ "tell it what to fill"

export function allItems(data, schema) {
  const idx = schema.items ? schema : indexSchema(schema);
  const refs = [];
  for (const sec of idx.schema.sections) {
    for (const item of sec.items) refs.push(item.key);
    if (sec.id === "student") refs.push("student.full_name", "student.last_first", "student.middle_initial", "student.full_name_middle");
    if (sec.id === "contact") refs.push("student.address_full", "student.city_state_zip");
    if (sec.id === "parent1") refs.push("parent1.full_name");
    if (sec.id === "parent2") refs.push("parent2.full_name");
    if (sec.id === "school") refs.push("school.counselor_full_name", "school.graduation_year", "school.graduation_month");
    if (sec.id === "academics" && (data?.lists?.courses || []).length)
      refs.push("courses.names.ap.any", "courses.count.ap.any", "courses.names.honors.any", "courses.count.honors.any", "courses.names.de.any",
        "courses.count.de.any", "courses.names.ib.any", "courses.names.all.now", "courses.count.all.any");
    if (sec.id === "academics" && (data?.lists?.exams || []).length) refs.push("exams.summary");
  }
  for (const list of idx.schema.lists) {
    const rows = data?.lists?.[list.id] || [];
    rows.forEach((r, i) => {
      if (list.id === "custom") refs.push(`custom.${i}`);
      else {
        for (const f of list.fields) refs.push(`${list.id}.${i}.${f.id}`);
        if (list.id === "recommenders") refs.push(`recommenders.${i}.full_name`);
      }
    });
  }
  return refs.map((r) => itemInfo(r, data, idx)).filter(Boolean);
}

const SEARCH_SYN = { parents: "parent", guardians: "guardian", guardian: "parent", mom: "parent", dad: "parent", mother: "parent", father: "parent",
  counsellor: "counselor", guidance: "counselor", names: "name", emails: "email", phones: "phone", scores: "score", grades: "gpa",
  cell: "phone", mobile: "phone", telephone: "phone", mail: "email", birthday: "birth", dob: "birth", zipcode: "zip", activities: "activity",
  awards: "award", honors: "award", jobs: "job", recommenders: "recommender", references: "recommender", reference: "recommender" };

export function searchItems(query, data, schema) {
  const idx = schema.items ? schema : indexSchema(schema);
  const q = tokens(query).filter((t) => !STOP.has(t) && t !== "my").map((t) => SEARCH_SYN[t] || t);
  if (!q.length) return [];
  const results = [];
  for (const info of allItems(data, idx)) {
    const sec = idx.schema.sections.find((s) => s.id === info.section) || idx.schema.lists.find((l) => l.id === info.section);
    const hay = new Set(tokens(`${info.label} ${sec?.title || ""} ${info.ref.replace(/[._]/g, " ")} ${info.section}`).map((t) => SEARCH_SYN[t] || t));
    if (info.section.startsWith("parent")) hay.add("parent");
    if (/counselor/.test(info.ref)) hay.add("counselor");
    if (/^academics\.(act|sat)/.test(info.ref)) hay.add("score");
    if (/gpa/.test(info.ref)) hay.add("gpa");
    if (/date_of_birth/.test(info.ref)) hay.add("birth");
    let score = 0;
    for (const t of q) {
      if (hay.has(t)) score += 2;
      else if ([...hay].some((h) => h.startsWith(t) && t.length >= 3)) score += 1;
      else { score = -1; break; }
    }
    if (score > 0) results.push({ ...info, score, display: displayValue(info, idx.states) });
  }
  return results.sort((a, b) => b.score - a.score).slice(0, 30);
}
