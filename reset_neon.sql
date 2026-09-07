-- Run this ONCE in the Neon SQL Editor AFTER deploying the updated code.
-- It permanently deletes all existing finance data and resets the identity counters.
-- Do not run this if you need to keep the existing data.

TRUNCATE TABLE expenses, income, budgets RESTART IDENTITY;
