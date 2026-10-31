import { BreakpointObserver } from '@angular/cdk/layout';
import { Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { MatButton, MatIconButton } from '@angular/material/button';
import { MatListItem, MatNavList } from '@angular/material/list';
import { MatMenu, MatMenuItem, MatMenuTrigger } from '@angular/material/menu';
import { MatSidenav, MatSidenavContainer, MatSidenavContent } from '@angular/material/sidenav';
import { MatToolbar } from '@angular/material/toolbar';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { map } from 'rxjs';
import { APP_TITLE } from '../app-title';
import { AuthService } from '../auth/auth.service';
import { PlayMenu } from '../leagues/play/play-menu';
import { GENERIC_ERROR_MESSAGE } from '../shared/form-errors';
import { injectNotify } from '../shared/notify';

/** At this width and wider the sidebar sits beside the page; below it is a drawer (ADR-0043). */
const WIDE = '(min-width: 768px)';

/** At this width and wider the account button has room for the email. */
const ROOMY = '(min-width: 600px)';

/** ADR-0043 layout B: the signed-in frame (header + sidebar) around every page but `/login`. */
@Component({
  imports: [
    MatButton,
    MatIconButton,
    MatListItem,
    MatMenu,
    MatMenuItem,
    MatMenuTrigger,
    MatNavList,
    MatSidenav,
    MatSidenavContainer,
    MatSidenavContent,
    MatToolbar,
    PlayMenu,
    RouterLink,
    RouterLinkActive,
    RouterOutlet,
  ],
  selector: 'app-shell',
  styleUrl: './shell.scss',
  templateUrl: './shell.html',
})
export class Shell {
  private readonly auth = inject(AuthService);
  private readonly notify = injectNotify();

  protected readonly title = APP_TITLE;
  protected readonly user = this.auth.currentUser;
  protected readonly wide = toSignal(
    inject(BreakpointObserver)
      .observe(WIDE)
      .pipe(map((state) => state.matches)),
    { initialValue: false },
  );

  /** At 600px and wider the account button shows the email; below it, just its initial. */
  protected readonly roomy = toSignal(
    inject(BreakpointObserver)
      .observe(ROOMY)
      .pipe(map((state) => state.matches)),
    { initialValue: false },
  );
  protected readonly initial = computed(() => this.user()?.email.charAt(0).toUpperCase() ?? '');

  protected unlinkSelfInsert(): void {
    this.auth.unlinkSelfInsert().subscribe({
      next: () => this.notify('Self-insert unlinked.'),
      error: () => this.notify(GENERIC_ERROR_MESSAGE),
    });
  }

  protected signOut(): void {
    // AuthService.logout() forgets the session and navigates to /login itself.
    this.auth.logout().subscribe();
  }
}
