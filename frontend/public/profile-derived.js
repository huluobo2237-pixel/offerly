// Preserve derived values when normalizing saved resume profiles.
export function syncDerivedProfile(profile, changedPath = '') {
  if (!profile._profileChoicesVersion) {
    const previous = {};
    if (profile.basic.graduateMajor)
      previous.graduateMajor = profile.basic.graduateMajor;
    if (profile.skillsSummary.certifications)
      previous.certifications = profile.skillsSummary.certifications;
    if (Object.keys(previous).length) {
      if (!profile._source || typeof profile._source !== 'object')
        profile._source = { previousSource: profile._source || '' };
      profile._source.previousDerivedValues = previous;
    }
    profile._profileChoicesVersion = 1;
  }
  const groups = [
    'education',
    'internships',
    'projects',
    'campus',
    'employment',
    'social',
    'awards',
  ];
  for (const group of groups)
    for (const [index, row] of (profile[group] || []).entries()) {
      const dateChanged = [
        `profile.${group}.${index}.start`,
        `profile.${group}.${index}.end`,
        `profile.${group}.${index}.date`,
      ].includes(changedPath);
      if (row._pdfDateRange && !dateChanged) continue;
      if (dateChanged) delete row._pdfDateRange;
      const start = group === 'awards' ? row.date : row.start;
      if (
        start ||
        row.end ||
        changedPath ===
          `profile.${group}.${index}.${group === 'awards' ? 'date' : 'start'}` ||
        changedPath === `profile.${group}.${index}.end`
      ) {
        row.dateRange = [start || '', row.end || '']
          .filter(Boolean)
          .join(' ~ ');
      }
    }
  const rank = {
    博士研究生: 5,
    博士: 5,
    硕士研究生: 4,
    硕士: 4,
    本科: 3,
    学士: 3,
    专科: 2,
    大专: 2,
    中专: 1,
    高中: 1,
    初中: 0,
    小学: 0,
  };
  const highest = (profile.education || [])
    .filter((row) => row.level)
    .reduce(
      (best, row) =>
        !best ||
        (rank[row.level] ?? -1) > (rank[best.level] ?? -1) ||
        ((rank[row.level] ?? -1) === (rank[best.level] ?? -1) &&
          String(row.end || '') > String(best.end || ''))
          ? row
          : best,
      null,
    );
  if (highest) profile.basic.graduateMajor = highest.major || '';
  else if (changedPath.startsWith('profile.education.'))
    profile.basic.graduateMajor = '';
  profile.skillsSummary.certifications = [
    ...new Set(
      (profile.certificates || []).map((row) => row.name).filter(Boolean),
    ),
  ].join('、');
  const now = new Date();
  for (const [index, row] of (profile.family || []).entries()) {
    if (/^\d{4}-\d{2}-\d{2}$/.test(row.birthDate || '')) {
      const [year, month, day] = row.birthDate.split('-').map(Number);
      const age =
        now.getFullYear() -
        year -
        (now.getMonth() + 1 < month ||
        (now.getMonth() + 1 === month && now.getDate() < day)
          ? 1
          : 0);
      const date = new Date(year, month - 1, day);
      row.age =
        date.getFullYear() === year &&
        date.getMonth() + 1 === month &&
        date.getDate() === day &&
        age >= 0
          ? String(age)
          : '';
    } else if (changedPath === `profile.family.${index}.birthDate`)
      row.age = '';
  }
}
