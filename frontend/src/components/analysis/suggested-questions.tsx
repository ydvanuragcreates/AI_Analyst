import type { DatasetProfile } from "@/types/dataset";

/**
 * Derives follow-up question suggestions from the dataset's real,
 * already-fetched profile (column names, kinds) -- there is no backend
 * field for LLM-authored follow-up suggestions on the v1 job-based API
 * (see the project write-up's "known limitations"), so this builds
 * genuinely dataset-specific suggestions from real column names instead
 * of a hardcoded, one-size-fits-all list.
 */
export function buildSuggestedQuestions(profile: DatasetProfile): string[] {
  const suggestions: string[] = [];
  const numeric = profile.numeric_columns[0];
  const categorical = profile.categorical_columns[0];
  const date = profile.date_columns[0];

  if (numeric) suggestions.push(`What is the total ${numeric}?`);
  if (numeric && categorical) suggestions.push(`Which ${categorical} has the highest ${numeric}?`);
  if (numeric && date) suggestions.push(`Did ${numeric} increase over time?`);
  if (profile.numeric_columns.length >= 2) suggestions.push(`Are ${profile.numeric_columns[0]} and ${profile.numeric_columns[1]} related?`);
  suggestions.push("Find anomalies in this dataset.");

  return suggestions.slice(0, 4);
}

export function SuggestedQuestions({ profile, onSelect }: { profile: DatasetProfile; onSelect: (question: string) => void }) {
  const suggestions = buildSuggestedQuestions(profile);
  if (suggestions.length === 0) return null;

  return (
    <div>
      <p className="mb-2 text-xs font-medium text-muted-foreground">You might want to ask:</p>
      <div className="flex flex-wrap gap-2">
        {suggestions.map((question) => (
          <button
            key={question}
            type="button"
            onClick={() => onSelect(question)}
            className="rounded-full border bg-background px-3 py-1.5 text-xs font-medium transition-colors hover:bg-accent"
          >
            {question}
          </button>
        ))}
      </div>
    </div>
  );
}
