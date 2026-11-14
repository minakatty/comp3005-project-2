SELECT did, COUNT(*) AS n, AVG(salary) AS avg_sal FROM Emp WHERE age > 30 GROUP BY did HAVING COUNT(*) > 20;
