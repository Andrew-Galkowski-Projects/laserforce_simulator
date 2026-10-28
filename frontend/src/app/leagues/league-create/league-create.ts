import { Component, inject, signal } from '@angular/core';
import { NonNullableFormBuilder, ReactiveFormsModule } from '@angular/forms';
import { MatButton } from '@angular/material/button';
import { MatError, MatFormField, MatHint, MatLabel } from '@angular/material/form-field';
import { MatInput } from '@angular/material/input';
import { MatOption, MatSelect } from '@angular/material/select';
import { Router, RouterLink } from '@angular/router';
import { Difficulty, LeagueTemplate } from '../../api/leagues.models';
import { LeaguesService } from '../../api/leagues.service';
import { apiMessage } from '../../shared/api-message';
import {
  GENERIC_ERROR_MESSAGE,
  REQUIRED_MESSAGE,
  applyApiErrors,
  clearServerErrors,
} from '../../shared/form-errors';
import { DIFFICULTY_CHOICES, DIFFICULTY_HELP } from './difficulty';

/**
 * `/leagues/new`: the template chooser, porting `leagues/create.html`. The API binds
 * `LeagueTemplateForm` for `template` and builds the rest of `CreateLeagueForm` from it.
 */
@Component({
  selector: 'app-league-create',
  imports: [
    ReactiveFormsModule,
    RouterLink,
    MatButton,
    MatError,
    MatFormField,
    MatHint,
    MatInput,
    MatLabel,
    MatOption,
    MatSelect,
  ],
  styleUrl: './league-create.scss',
  templateUrl: './league-create.html',
})
export class LeagueCreate {
  private readonly fb = inject(NonNullableFormBuilder);
  private readonly leagues = inject(LeaguesService);
  private readonly router = inject(Router);

  protected readonly difficultyChoices = DIFFICULTY_CHOICES;
  protected readonly difficultyHelp = DIFFICULTY_HELP;
  protected readonly requiredMessage = REQUIRED_MESSAGE;

  /** Control names match the API body; `required` / `maxlength` sit on the template's inputs. */
  protected readonly form = this.fb.group({
    template: [''],
    league_name: [''],
    difficulty: ['medium' as Difficulty],
  });

  /** The curated presets; null until loaded. */
  protected readonly templates = signal<LeagueTemplate[] | null>(null);
  protected readonly loadError = signal<string | null>(null);
  protected readonly busy = signal(false);
  protected readonly nonFieldErrors = signal<string[]>([]);

  constructor() {
    this.leagues.templates().subscribe({
      next: (templates) => {
        this.templates.set(templates);
        // The Django select has no empty option, so its first template is the default.
        if (templates.length > 0) this.form.controls.template.setValue(templates[0].key);
      },
      error: (error: unknown) => this.loadError.set(apiMessage(error, GENERIC_ERROR_MESSAGE)),
    });
  }

  protected create(): void {
    this.nonFieldErrors.set([]);
    clearServerErrors(this.form);
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    this.busy.set(true);
    this.leagues.create(this.form.getRawValue()).subscribe({
      next: (league) => void this.router.navigate(['/leagues', league.id]),
      error: (error: unknown) => {
        this.busy.set(false);
        this.nonFieldErrors.set(applyApiErrors(this.form, error));
      },
    });
  }
}
