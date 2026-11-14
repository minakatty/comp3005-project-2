SELECT t.did, t.n
  FROM (SELECT did, COUNT(*) AS n FROM Emp GROUP BY did) AS t
 WHERE t.n > 3;
