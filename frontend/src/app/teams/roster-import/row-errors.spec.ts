import { ApiError } from '../../api/api-client';
import { classifyImportErrors } from './row-errors';

describe('classifyImportErrors', () => {
  it('formats row items in the server order: row + column, row only, and row 0 as File', () => {
    const error = new ApiError(400, [
      { code: 'invalid', message: 'Unknown role "pilot".', field: 'row:1:role' },
      { code: 'invalid', message: 'Row has 7 columns, expected 8.', field: 'row:3' },
      { code: 'invalid', message: 'Missing column "role".', field: 'row:0:role' },
      { code: 'invalid', message: 'Too many rows.', field: 'row:0' },
    ]);
    expect(classifyImportErrors(error)).toEqual({
      file: [],
      rows: [
        'Row 1 (role): Unknown role "pilot".',
        'Row 3: Row has 7 columns, expected 8.',
        'File: Missing column "role".',
        'File: Too many rows.',
      ],
      alert: [],
    });
  });

  it('puts csv_file items on the file control and everything else in the alert line', () => {
    const error = new ApiError(400, [
      { code: 'required', message: 'No file was submitted.', field: 'csv_file' },
      { code: 'invalid', message: 'Nothing to import.', field: null },
      { code: 'invalid', message: 'Unexpected field.', field: 'rows' },
    ]);
    expect(classifyImportErrors(error)).toEqual({
      file: ['No file was submitted.'],
      rows: [],
      alert: ['Nothing to import.', 'Unexpected field.'],
    });
  });

  it('gives one generic alert for a non-ApiError', () => {
    expect(classifyImportErrors(new Error('boom'))).toEqual({
      file: [],
      rows: [],
      alert: ['Something went wrong. Please try again.'],
    });
  });
});
