import tseslint from "typescript-eslint";

export default tseslint.config(
    {
        ignores: ["dist/**", "public/**"],
    },
    ...tseslint.configs.recommended,
);
