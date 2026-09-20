export type ImportStage = 1 | 2 | 3 | 4 | 5;

const STAGES = [
  { label: "Upload", detail: "Choose source evidence" },
  { label: "Identify / resolve", detail: "Confirm the destination" },
  { label: "Parse", detail: "Check extracted evidence" },
  { label: "Review", detail: "Decide what is ready" },
  { label: "Commit", detail: "Write confirmed records" },
] as const;

type Props = {
  current?: ImportStage;
  completeThrough?: 0 | ImportStage;
  label?: string;
};

/** A presentation-only map of an import workflow.
 *
 * The two import products deliberately keep separate state machines and request bodies. This
 * component only gives those states one vocabulary; callers remain responsible for mapping their
 * own authenticated state to a current and completed stage.
 */
export function ImportStageJourney({
  current,
  completeThrough = 0,
  label = "Import steps",
}: Props) {
  return (
    <ol aria-label={label} className="import-stage-journey">
      {STAGES.map((stage, index) => {
        const number = (index + 1) as ImportStage;
        return (
          <li
            aria-current={current === number ? "step" : undefined}
            data-complete={number <= completeThrough}
            key={stage.label}
          >
            <span aria-hidden="true" className="import-stage-number">{number}</span>
            <span className="import-stage-copy">
              <strong>{stage.label}</strong>
              <small>{stage.detail}</small>
            </span>
          </li>
        );
      })}
    </ol>
  );
}
