// Saved-profile compatibility only. The current UI uses the PDF module editor.
function section(id, keys, repeat = false) {
  return {
    id,
    ...(repeat ? { repeat: true } : {}),
    fields: keys
      .trim()
      .split(/\s+/)
      .map((value) => {
        const [key, path] = value.split(":");
        return path ? { key, path } : { key };
      }),
  };
}

const legacySections = [
  section(
    "basic",
    `
    name englishName formerName gender birthDate nationality ethnicity idType idNumber
    idExpiry politicalStatus partyDate maritalStatus height weight health hometown household
    householdType studentOrigin residence address postcode driverLicense
  `,
  ),
  section(
    "contact",
    `
    phoneCode phone backupPhone email backupEmail wechat qq github linkedin website
    emergencyName emergencyRelation emergencyPhone emergencyAddress
  `,
  ),
  section(
    "intent",
    `
    status graduationYear highestEducation workYears desiredRole desiredIndustry
    desiredCities salaryMonthly salaryAnnual salaryNotes arrivalDate noticePeriod
    internshipStart internshipMonths daysPerWeek relocate travel rotation adjustment
    workPermit noncompete interviewAvailability preferredCompany
  `,
  ),
  section(
    "education",
    `
    school college countryCity schoolType level degree major majorCategory minor start end
    duration studyMode unified gpa gpaMax averageScore rank rankPercent courses thesis
    supervisor honors graduationCertificate degreeCertificate verification
  `,
    true,
  ),
  section(
    "internships",
    `
    company department role city start end present industry duties results technologies
    supervisor referencePhone reason
  `,
    true,
  ),
  section(
    "employment",
    `
    company department role type industry city start end present duties results teamSize
    salary reason referenceName referencePhone
  `,
    true,
  ),
  section(
    "projects",
    `
    name role company start end teamSize stack background duties results url repository
  `,
    true,
  ),
  section(
    "campus",
    `
    type organization department role start end duties results
  `,
    true,
  ),
  section(
    "languages",
    `
    language exam score level date listening speaking reading writing certificateNumber
    expiry
  `,
    true,
  ),
  section(
    "skills",
    `
    category name level years evidence
  `,
    true,
  ),
  section(
    "certificates",
    `
    name level issuer number date expiry url description
  `,
    true,
  ),
  section(
    "awards",
    `
    name level prize issuer date contribution
  `,
    true,
  ),
  section(
    "research",
    `
    type title organization authorOrder date identifier status url description
  `,
    true,
  ),
  section(
    "family",
    `
    name relation birthDate company role phone politicalStatus notes
  `,
    true,
  ),
  section(
    "portfolio",
    `
    title type url description
  `,
    true,
  ),
  section(
    "extra",
    `
    introduction evaluation strengths improvements hobbies careerPlan motivation achievement
    challenge referralName referralCode source relativesInCompany previousApplications notes
  `,
  ),
  section(
    "questions",
    `
    company question answer
  `,
    true,
  ),
];

export const sections = [
  section(
    "basic",
    `
    name pinyin englishName gender phone:profile.contact.phone email:profile.contact.email
    birthDate age nationality ethnicity politicalStatus maritalStatus idType idNumber
    residence hometown studentOrigin householdType household
    highestEducation:profile.intent.highestEducation degree graduateSchool graduateMajor
    studyMode graduationDate freshGraduate status:profile.intent.status
    workYears:profile.intent.workYears previousCompany wechat:profile.contact.wechat
    qq:profile.contact.qq website:profile.contact.website englishLevel entranceDate
    entranceScore entranceSubjects technicalRank emergencyName:profile.contact.emergencyName
    emergencyRelation:profile.contact.emergencyRelation
    emergencyPhone:profile.contact.emergencyPhone postcode health longTermIllness
    criminalRecord militaryService veteran bloodType partyJoinDate recommendedGraduate
    studyAbroad financialHardship colorWeakness partyDate archiveLocation height weight
    evaluation:profile.extra.evaluation strengths:profile.extra.strengths
    hobbies:profile.extra.hobbies careerPlan:profile.extra.careerPlan foreignStatus
  `,
  ),
  section(
    "intent",
    `
    desiredRole desiredIndustry currentIndustry arrivalDate desiredCities interviewCity
    salaryMonthly salaryAnnual currentSalary relativesInCompany source:profile.extra.source
    adjustment
  `,
  ),
  section(
    "education",
    `
    school level college major majorCategory countryCity schoolType status duration start end
    dateRange awardDate degree degreeType degreeCategory studyMode admissionType unified gpa
    failedCourses classRankPercent rankPercent graduationCertificate degreeCertificate
    studentNumber supervisor lab keyLab jointTraining counselor counselorPhone overseas
    description majorDescription courses researchDirection thesis
  `,
    true,
  ),
  section(
    "internships",
    `
    company companySize overseas department role start end dateRange city industry
    employmentType annualSalary supervisor referencePhone referenceRole duties reason
  `,
    true,
  ),
  section(
    "projects",
    `
    name teamSize overseas role practiceMode start end dateRange city url referenceName
    referencePhone referenceRole background duties results
  `,
    true,
  ),
  section(
    "campus",
    `
    organization level role start end dateRange duties results
  `,
    true,
  ),
  section(
    "employment",
    `
    company companySize overseas department role start end dateRange city industry type
    salary annualSalary referenceName referencePhone referenceRole duties reason
  `,
    true,
  ),
  section(
    "social",
    `
    organization role start end dateRange duties
  `,
    true,
  ),
  section(
    "skillsSummary",
    `
    technical languages software certifications soft
  `,
  ),
  section(
    "certificates",
    `
    name type date number issuer score expiry description
  `,
    true,
  ),
  section(
    "languages",
    `
    language date exam score level listeningSpeaking readingWriting
  `,
    true,
  ),
  section(
    "family",
    `
    name relation gender education ethnicity idNumber birthDate age phone company role
    politicalStatus address
  `,
    true,
  ),
  section(
    "awards",
    `
    name issuer date end dateRange level type prize contribution
  `,
    true,
  ),
  section(
    "patents",
    `
    title identifier date status inventors description
  `,
    true,
  ),
  section(
    "papers",
    `
    title organization authors volume impactFactor level authorOrder date identifier url
    description
  `,
    true,
  ),
];
export const attachmentTypes = [
  {
    id: "resume",
    name: "附件简历",
    description: "必须选择 · 用于正式投递的简历版本",
    accept: ".pdf,.jpg,.jpeg,.png",
    required: true,
    multiple: false,
  },
  {
    id: "englishResume",
    name: "英文 PDF 简历",
    description: "申请海外或英语岗位时使用",
    accept: ".pdf",
    multiple: false,
  },
  {
    id: "transcript",
    name: "成绩单",
    description: "本科 / 研究生 / 其他学段成绩单",
    accept: ".pdf,.jpg,.jpeg,.png",
    multiple: true,
  },
  {
    id: "cet4",
    name: "英语四级成绩单",
    description: "CET-4 成绩报告或证书",
    accept: ".pdf,.jpg,.jpeg,.png",
    multiple: true,
  },
  {
    id: "cet6",
    name: "英语六级成绩单",
    description: "CET-6 成绩报告或证书",
    accept: ".pdf,.jpg,.jpeg,.png",
    multiple: true,
  },
  {
    id: "language",
    name: "其他语言成绩",
    description: "雅思、托福、日语等成绩证明",
    accept: ".pdf,.jpg,.jpeg,.png",
    multiple: true,
  },
  {
    id: "certificates",
    name: "资格证书 / 获奖证明",
    description: "职业认证、计算机等级、竞赛获奖等",
    accept: ".pdf,.jpg,.jpeg,.png",
    multiple: true,
  },
  {
    id: "education",
    name: "学历 / 学籍证明",
    description: "毕业证、学位证、学信网或留服证明",
    accept: ".pdf,.jpg,.jpeg,.png",
    multiple: true,
  },
  {
    id: "internship",
    name: "实习 / 工作证明",
    description: "实习证明、推荐信等",
    accept: ".pdf,.jpg,.jpeg,.png",
    multiple: true,
  },
  {
    id: "portfolio",
    name: "作品集文件",
    description: "作品集 PDF、图片或压缩包",
    accept: ".pdf,.jpg,.jpeg,.png,.zip",
    multiple: true,
  },
  {
    id: "photo",
    name: "证件照",
    description: "用于企业要求的照片上传",
    accept: ".jpg,.jpeg,.png",
    multiple: false,
  },
  {
    id: "other",
    name: "其他附件",
    description: "求职信、其他企业要求的材料",
    accept: ".pdf,.jpg,.jpeg,.png,.zip",
    multiple: true,
  },
];
export function blankItem(section) {
  return Object.fromEntries(
    section.fields.filter((f) => !f.path).map((f) => [f.key, ""]),
  );
}

export function emptyProfile() {
  const profile = Object.fromEntries(
    legacySections.map((s) => [s.id, s.repeat ? [] : blankItem(s)]),
  );
  for (const s of sections) {
    profile[s.id] = s.repeat ? [] : { ...profile[s.id], ...blankItem(s) };
    for (const f of s.fields) {
      if (f.path) {
        const [, group, key] = f.path.split(".");
        profile[group] ??= {};
        profile[group][key] ??= "";
      }
    }
  }
  return profile;
}
export function normalizeProfile(saved = {}) {
  const profile = { ...emptyProfile(), ...saved };
  for (const s of [...legacySections, ...sections]) {
    const base = blankItem(s);
    profile[s.id] = s.repeat
      ? Array.isArray(saved[s.id])
        ? saved[s.id].map((item) => ({ ...base, ...item }))
        : []
      : { ...base, ...(saved[s.id] || {}) };
  }
  for (const s of sections)
    for (const f of s.fields)
      if (f.path) {
        const [, group, key] = f.path.split(".");
        profile[group] ??= {};
        profile[group][key] ??= "";
      }
  if (saved._profileSchemaVersion !== 3) {
    if (!profile.extra.evaluation)
      profile.extra.evaluation = profile.extra.introduction || "";
    if (!profile.contact.website) {
      const link = (profile.portfolio || []).find((p) => p.url);
      profile.contact.website = link?.url || profile.contact.github || "";
    }
    if (!profile.skillsSummary.technical)
      profile.skillsSummary.technical = (profile.skills || [])
        .map((s) => [s.name, s.level, s.evidence].filter(Boolean).join("："))
        .filter(Boolean)
        .join("\n");
    if (!saved.social)
      profile.social = (profile.campus || [])
        .filter((s) => s.type === "社会实践" || s.type === "志愿服务")
        .map((s) => ({ ...s }));
    if (!saved.patents)
      profile.patents = (profile.research || [])
        .filter((s) => s.type === "专利")
        .map((s) => ({ ...s }));
    if (!saved.papers)
      profile.papers = (profile.research || [])
        .filter((s) => s.type === "论文")
        .map((s) => ({ ...s }));
  }
  profile._profileSchemaVersion = 3;
  return profile;
}
