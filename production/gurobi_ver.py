import gurobipy as gp
from gurobipy import GRB
import pandas as pd

def schedule(df):
    env = gp.Env(empty=True)
    env.setParam('OutputFlag', 0)
    env.start()
    md = gp.Model(env=env)
    se, job_id, machine_id = {}, df.JobID, list(df.columns)[1:]
    total_job_num = len(job_id)
    total_makespan = md.addVar(vtype=GRB.INTEGER)
    for job_idx in range(total_job_num):
        job = job_id[job_idx]
        se[job] = {}

        for m_idx in range(len(machine_id)):
            m = machine_id[m_idx]
            se[job][m] = [md.addVar(vtype=GRB.INTEGER) for _ in range(2)]
            md.addConstrs(se[job][m][event] >= 0 for event in (0, 1))
            md.addConstr(se[job][m][1] == se[job][m][0] + df.loc[df.JobID == job, m].values[0])
            if m_idx:
                prior_m = machine_id[m_idx - 1]
                md.addConstr(se[job][m][0] >= se[job][prior_m][1])
            md.addConstr(total_makespan >= se[job][m][1])
    seq = {}
    for job1_idx in range(total_job_num):
        job1 = job_id[job1_idx]
        seq[job1] = {}
        for job2_idx in range(job1_idx + 1, total_job_num):
            job2 = job_id[job2_idx]
            seq[job1][job2] = md.addVar(vtype=GRB.BINARY)
            for m in machine_id:
                md.addGenConstrIndicator(seq[job1][job2], True, se[job1][m][1] <= se[job2][m][0])
                md.addGenConstrIndicator(seq[job1][job2], False, se[job1][m][0] >= se[job2][m][1])
    md.setObjective(total_makespan, GRB.MINIMIZE)
    md.optimize()
    print(md.Status)
    print(md.ObjVal)
    return

def main():

    scheduling = {}
    for week, amount in (("tue",85993), ("wed",85993), ("thu",85993)):
        directory = "./ProcessingTimeTable/t2_500_20_"
        df = pd.read_csv(directory + week + ".csv")

        total_job = amount//1000 - 1
        df = df.loc[:total_job, :]
        schedule(df)
    print(scheduling)

if __name__ == "__main__":
    main()