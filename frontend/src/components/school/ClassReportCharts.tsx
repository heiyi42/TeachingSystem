import { useState } from "react";
import type { ClassReport } from "../../schoolTypes";

const percent = (value: number, total: number) => total ? value / total * 100 : 0;
const formatPercent = (value: number, total: number) => `${percent(value, total).toFixed(1)}%`;

export function ClassReportCharts({ report }: { report: ClassReport }) {
  const [mode, setMode] = useState("completion");
  const counts = report.totals;
  const total = mode === "completion" ? counts.expected_answers : counts.passed;
  const slices = mode === "completion" ? [
    { label: "已提交", value: counts.submitted, color: "#3f6f5a" },
    { label: "未提交", value: counts.unsubmitted, color: "#b7c0ba" },
  ] : [
    { label: "独立通过", value: counts.independent, color: "#3f6f5a" },
    { label: "辅助通过", value: counts.assisted, color: "#688a9b" },
    { label: "订正通过", value: counts.corrected, color: "#b39861" },
    { label: "重复作答通过", value: counts.repeated, color: "#87739e" },
    { label: "辅助信息缺失", value: counts.unclassified_pass, color: "#b7c0ba" },
  ];
  let offset = 0;

  return <section className="class-report-charts" aria-label="班级学情图表">
    <figure>
      <figcaption><h3>章节作答情况</h3></figcaption>
      <p className="training-muted">分母为各章应作答题次。独立通过属于已提交作答的一部分，两项不能相加。</p>
      <div className="report-bar-legend"><span>已提交</span><span>独立通过</span></div>
      {report.chapters.length ? <>
        <div className="report-bar-axis" aria-hidden="true"><span>0%</span><span>50%</span><span>100%</span></div>
        {report.chapters.map(chapter => <div className="report-bar-group" key={chapter.id}>
          <strong>{chapter.title}</strong>
          {(["submitted", "independent"] as const).map(key => <div key={key} className={`report-bar-row report-bar-${key}`}>
            <span>{key === "submitted" ? "已提交" : "独立通过"} · {chapter[key]}/{chapter.expected_answers} 题次</span>
            <div className="report-bar-track" role="img" aria-label={`${chapter.title}，${key === "submitted" ? "已提交" : "独立通过"} ${chapter[key]}，应作答 ${chapter.expected_answers} 题次${chapter.expected_answers ? `，${formatPercent(chapter[key], chapter.expected_answers)}` : "，暂无比例"}`}>
              <div style={{ width: `${percent(chapter[key], chapter.expected_answers)}%` }} />
            </div>
            <span>{chapter.expected_answers ? formatPercent(chapter[key], chapter.expected_answers) : "—"}</span>
          </div>)}
        </div>)}
      </> : <p>暂无已布置章节，尚无可统计数据。</p>}
    </figure>
    <figure>
      <figcaption><h3>作答分布</h3></figcaption>
      <label className="report-chart-mode">统计内容
        <select value={mode} onChange={event => setMode(event.target.value)}>
          <option value="completion">完成情况</option>
          <option value="passes">通过类型</option>
        </select>
      </label>
      <p className="training-muted">分母：{mode === "completion" ? "应作答" : "已通过"} {total} 题次，各分类互斥。</p>
      {total ? <div className="report-donut-layout">
        <svg className="report-donut" viewBox="0 0 200 200" role="img" aria-label={slices.map(slice => `${slice.label} ${slice.value} 题次，${formatPercent(slice.value, total)}`).join("；")}>
          {slices.filter(slice => slice.value > 0).map(slice => {
            const start = offset;
            const size = percent(slice.value, total);
            offset += size;
            return <circle key={slice.label} cx="100" cy="100" r="74" fill="none" stroke={slice.color} strokeWidth="28" pathLength="100" strokeDasharray={`${size} ${100 - size}`} strokeDashoffset={-start} transform="rotate(-90 100 100)" />;
          })}
          <text x="100" y="98" textAnchor="middle" className="report-donut-total">{total}</text>
          <text x="100" y="121" textAnchor="middle">{mode === "completion" ? "应作答题次" : "通过题次"}</text>
        </svg>
        <ul className="report-donut-legend">{slices.map(slice => <li key={slice.label}>
          <span className="report-chart-swatch" style={{ background: slice.color }} aria-hidden="true" />
          <span>{slice.label}</span><span>{slice.value} · {formatPercent(slice.value, total)}</span>
        </li>)}</ul>
      </div> : <p className="report-chart-empty">{mode === "completion" ? "暂无应作答数据" : "尚无通过作答"}，暂不显示占比。</p>}
    </figure>
  </section>;
}
