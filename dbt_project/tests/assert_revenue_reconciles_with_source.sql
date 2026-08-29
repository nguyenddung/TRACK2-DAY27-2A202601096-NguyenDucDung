-- Singular business test (transformation logic, not a column constraint):
-- total mart revenue must reconcile with the total amount of completed
-- orders in the staging source-of-truth. A join fan-out -- e.g. a customer
-- dimension with two active rows for the same customer_id -- silently
-- inflates this total without ever tripping a not_null/unique/accepted
-- generic test, because every individual column value stays valid.
with source_total as (
    select sum(amount_usd) as total_amount
    from {{ ref('stg_orders') }}
    where status = 'completed'
),
mart_total as (
    select sum(daily_revenue) as total_amount
    from {{ ref('fct_daily_revenue') }}
)
select
    source_total.total_amount as source_total_amount,
    mart_total.total_amount as mart_total_amount
from source_total, mart_total
where abs(source_total.total_amount - mart_total.total_amount) > 0.01
