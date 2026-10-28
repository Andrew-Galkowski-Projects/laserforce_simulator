import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Subject, of, throwError } from 'rxjs';
import { environment } from '../../environments/environment';
import { ApiError } from '../api/api-client';
import { CsvImport } from './csv-import';
import { GENERIC_ERROR_MESSAGE } from './form-errors';

const TEMPLATE_URL = `${environment.apiBaseUrl}/teams/import/template/`;

describe('CsvImport', () => {
  let http: HttpTestingController;
  let csv: CsvImport;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
    csv = TestBed.runInInjectionContext(() => new CsvImport());
  });

  afterEach(() => http.verify());

  const file = (name = 'roster.csv'): File => new File(['team,name\n'], name, { type: 'text/csv' });

  /** A `change` from a hidden file input holding `files`. */
  function changeEvent(files: File[]): { event: Event; input: HTMLInputElement } {
    const input = document.createElement('input');
    input.type = 'file';
    Object.defineProperty(input, 'files', { value: files, configurable: true });
    const event = new Event('change');
    Object.defineProperty(event, 'target', { value: input });
    return { event, input };
  }

  describe('choose', () => {
    it('keeps the picked File and clears the input so the same name re-fires', () => {
      const picked = file('spring.csv');
      const { event, input } = changeEvent([picked]);
      csv.choose(event);
      expect(csv.file()).toBe(picked);
      expect(input.value).toBe('');
    });

    it('keeps no File when the picker was cancelled', () => {
      csv.file.set(file());
      csv.choose(changeEvent([]).event);
      expect(csv.file()).toBeNull();
    });
  });

  describe('downloadTemplate', () => {
    it('saves the template as roster_template.csv', () => {
      const url = URL as unknown as Record<string, unknown>;
      const before = { create: url['createObjectURL'], revoke: url['revokeObjectURL'] };
      url['createObjectURL'] = vi.fn(() => 'blob:template');
      url['revokeObjectURL'] = vi.fn();
      const downloads: string[] = [];
      const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
        this: HTMLAnchorElement,
      ) {
        downloads.push(this.download);
      });
      try {
        csv.downloadTemplate();
        const req = http.expectOne(TEMPLATE_URL);
        expect(req.request.method).toBe('GET');
        req.flush(new Blob(['team,name\n'], { type: 'text/csv' }));
        expect(downloads).toEqual(['roster_template.csv']);
        expect(csv.alertErrors()).toEqual([]);
      } finally {
        click.mockRestore();
        url['createObjectURL'] = before.create;
        url['revokeObjectURL'] = before.revoke;
      }
    });

    it('puts a failed download in the alert line, replacing the earlier alert', () => {
      csv.alertErrors.set(['Old.']);
      csv.downloadTemplate();
      http
        .expectOne(TEMPLATE_URL)
        .flush(new Blob(['nope']), { status: 500, statusText: 'Server Error' });
      expect(csv.alertErrors()).toEqual(['The server responded with 500.']);
    });
  });

  describe('upload', () => {
    it('sends nothing without a file', () => {
      const send = vi.fn(() => of('ok'));
      csv.upload(send, () => undefined);
      expect(send).not.toHaveBeenCalled();
      expect(csv.busy()).toBe(false);
    });

    it('is busy while in flight, ignores a second upload, then clears the file and calls done', () => {
      const picked = file();
      csv.file.set(picked);
      csv.rowErrors.set(['Row 1: old.']);
      const response = new Subject<number>();
      const send = vi.fn(() => response);
      const done = vi.fn();
      csv.upload(send, done);
      expect(send).toHaveBeenCalledWith(picked);
      expect(csv.busy()).toBe(true);
      expect(csv.rowErrors()).toEqual([]);
      csv.upload(send, done);
      expect(send).toHaveBeenCalledTimes(1);
      response.next(3);
      expect(done).toHaveBeenCalledWith(3);
      expect(csv.busy()).toBe(false);
      expect(csv.file()).toBeNull();
    });

    it('sorts a failure into the file, row and alert lists and keeps the file', () => {
      const picked = file();
      csv.file.set(picked);
      const error = new ApiError(400, [
        { code: 'invalid', message: 'Not UTF-8.', field: 'csv_file' },
        { code: 'invalid', message: 'Unknown role.', field: 'row:2:role' },
        { code: 'invalid', message: 'Nothing to import.', field: null },
      ]);
      const done = vi.fn();
      csv.upload(() => throwError(() => error), done);
      expect(done).not.toHaveBeenCalled();
      expect(csv.busy()).toBe(false);
      expect(csv.file()).toBe(picked);
      expect(csv.fileErrors()).toEqual(['Not UTF-8.']);
      expect(csv.rowErrors()).toEqual(['Row 2 (role): Unknown role.']);
      expect(csv.alertErrors()).toEqual(['Nothing to import.']);
    });

    it('shows the generic message for a failure that is not an ApiError', () => {
      csv.file.set(file());
      csv.upload(
        () => throwError(() => new TypeError('boom')),
        () => undefined,
      );
      expect(csv.alertErrors()).toEqual([GENERIC_ERROR_MESSAGE]);
    });
  });

  it('reset drops the file and every error', () => {
    csv.file.set(file());
    csv.alertErrors.set(['a']);
    csv.fileErrors.set(['f']);
    csv.rowErrors.set(['r']);
    csv.reset();
    expect([csv.file(), csv.alertErrors(), csv.fileErrors(), csv.rowErrors()]).toEqual([
      null,
      [],
      [],
      [],
    ]);
  });
});
