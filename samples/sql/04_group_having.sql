SELECT did, COUNT(*) AS n, AVG(salary) AS avg_sal
  FROM Emp
 WHERE age > 25
 GROUP BY did
HAVING COUNT(*) > 5
 ORDER BY n DESC
 LIMIT 10;
