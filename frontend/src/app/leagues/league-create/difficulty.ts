// `CreateLeagueForm.difficulty`, shared by the template chooser and the Advanced form.
import { DIFFICULTY_LABELS, Difficulty } from '../../api/leagues.models';

export interface Choice<T> {
  value: T;
  label: string;
}

/** `DIFFICULTY_CHOICES`, in select order. */
export const DIFFICULTY_CHOICES: readonly Choice<Difficulty>[] = (
  ['easy', 'medium', 'hard'] as const
).map((value) => ({ value, label: DIFFICULTY_LABELS[value] }));

/** The Difficulty help text both `create.html` and `create_advanced.html` show. */
export const DIFFICULTY_HELP =
  'Picks which of the generated teams becomes your career team — Easy gives you the ' +
  'strongest, Medium the middle, Hard the weakest.';
