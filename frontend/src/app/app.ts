import { Component, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { MatToolbar } from '@angular/material/toolbar';
import { NavigationEnd, Router, RouterOutlet } from '@angular/router';
import { filter, map } from 'rxjs';
import { APP_TITLE } from './app-title';

@Component({
  imports: [MatToolbar, RouterOutlet],
  selector: 'app-root',
  styleUrl: './app.scss',
  templateUrl: './app.html',
})
export class App {
  protected readonly title = APP_TITLE;

  private readonly router = inject(Router);
  // ANG-04b prototype: the style-sample layouts bring their own shell, so hide the toolbar there.
  protected readonly onPrototype = toSignal(
    this.router.events.pipe(
      filter((e): e is NavigationEnd => e instanceof NavigationEnd),
      map((e) => e.urlAfterRedirects.startsWith('/prototype/')),
    ),
    { initialValue: false },
  );
}
