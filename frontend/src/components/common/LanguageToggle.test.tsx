import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import i18next from '@/i18n';

import { LanguageToggle } from './LanguageToggle';

describe('LanguageToggle', () => {
  it('renders both locales and marks the active one', () => {
    render(<LanguageToggle />);

    const hebrew = screen.getByRole('button', { name: 'עברית' });
    const english = screen.getByRole('button', { name: 'English' });

    expect(hebrew).toHaveAttribute('aria-current', 'true');
    expect(english).not.toHaveAttribute('aria-current');
  });

  it('flips the document direction when switching language', async () => {
    // The behaviour that matters: a locale change must reach `<html dir>`,
    // because that is what makes every logical CSS property resolve correctly.
    const user = userEvent.setup();
    render(<LanguageToggle />);

    expect(document.documentElement.getAttribute('dir')).toBe('rtl');

    await user.click(screen.getByRole('button', { name: 'English' }));

    expect(document.documentElement.getAttribute('dir')).toBe('ltr');
    expect(document.documentElement.getAttribute('lang')).toBe('en');

    await user.click(screen.getByRole('button', { name: 'עברית' }));

    expect(document.documentElement.getAttribute('dir')).toBe('rtl');
    expect(document.documentElement.getAttribute('lang')).toBe('he');
  });

  it('exposes an accessible group label', () => {
    render(<LanguageToggle />);
    expect(screen.getByRole('group', { name: i18next.t('language.label') })).toBeInTheDocument();
  });
});
