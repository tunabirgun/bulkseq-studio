export const exampleReports = [
  {label:'Standard report',url:'examples/standard/results/reports/results_report.html',bundle:'examples/standard-example.zip'},
  {label:'Meta-analysis report',url:'examples/meta/results/reports/meta_analysis_report.html',bundle:'examples/meta-example.zip'},
];

export function reportLinks(downloads=false) {
  return `<div class="example-report-links">${exampleReports.map(report=>`<p><a href="${report.url}">${report.label} →</a>${downloads?`<a class="example-bundle" href="${report.bundle}" aria-label="Download ${report.label.toLowerCase()} example files" download>Download example files</a>`:''}</p>`).join('')}</div>`;
}
