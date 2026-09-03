import type { ScarInspection } from "../api/types";
import { Markdown } from "../components/Markdown";
import { Icon } from "../shell/icons";

export function ScarDiagnosis(props: { inspection: ScarInspection }) {
  return (
    <section class="scar-diagnosis" aria-labelledby="scar-diagnosis-title">
      <div class="scar-why">
        <span class="scar-why-icon" aria-hidden="true"><Icon name="nav.scars" size={19} /></span>
        <div>
          <span class="eyebrow">Why this Scar exists</span>
          <h2 id="scar-diagnosis-title">Why it triggered</h2>
          <p>{props.inspection.explanation}</p>
        </div>
      </div>
      <div class="scar-comparison">
        <article class="scar-problem">
          <span>What went wrong</span>
          <Markdown content={props.inspection.description} />
        </article>
        <article class="scar-expectation">
          <span>What should happen</span>
          <Markdown content={props.inspection.expected_behavior} />
        </article>
      </div>
    </section>
  );
}
