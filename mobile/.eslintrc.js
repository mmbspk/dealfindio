module.exports = {
  extends: 'expo',
  root: true,
  overrides: [
    {
      files: ['**/__tests__/**/*.{js,ts,tsx}', '**/*.test.{js,ts,tsx}'],
      env: { jest: true },
    },
  ],
};
