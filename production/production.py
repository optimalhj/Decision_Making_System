import pandas as pd
import numpy as np
import random
from collections import deque

class Meta:
    def __init__(self, df):
        self.job_id = df.JobID
        self.machine_id = list(df.columns)[1:]
        self.df = df

    def total_makespan(self, gene):
        machine_start = {m: 0 for m in self.machine_id}
        job_start = {job: 0 for job in self.job_id}

        for job in gene:
            for m in self.machine_id:
                # print("Job :", job, "  /   Machine :", m)
                start_time = max(machine_start[m], job_start[job])
                # print(machine_start[m], job_start[job], f" -->   {start_time}", end=" + ")
                processing_time = self.df.loc[self.df.JobID == job, m].values
                # print(f"{processing_time} == ", end="")
                machine_start[m], job_start[job] = [start_time + processing_time for _ in range(2)]
            #     print(machine_start[m])
            #     print()
            # print()
        if max(machine_start.values()) != max(job_start.values()):
            print("Deep ERROR" * 100)
        return max(machine_start.values())

def mutation(off):
    off = list(off)
    if random.random() < 0.3:
        mutation_num = random.randint(1, 10)
        tabu_list = {}
        for _ in range(mutation_num):
            switch_indices = tuple(sorted(random.sample(list(range(len(off))), k=2)))
            if switch_indices in tabu_list:
                count = 0
                while switch_indices in tabu_list:
                    switch_indices = tuple(sorted(random.sample(list(range(len(off))), k=2)))
                    if count == 10:
                        break
                    if switch_indices not in tabu_list:
                        tabu_list[switch_indices] = 1
                        off[switch_indices[0]], off[switch_indices[1]] = off[switch_indices[1]], off[switch_indices[0]]
                        break
            else:
                tabu_list[switch_indices] = 1
                off[switch_indices[0]], off[switch_indices[1]] = off[switch_indices[1]], off[switch_indices[0]]
    return tuple(off)

def crossover(pr1, pr2):
    return_job = []
    cross = random.random()
    if cross < 0.5 or len(pr1)-1 // 2 <= 2:

        for idx in range(random.randint(1,len(pr1)-1)):
            return_job.append(pr1[idx])
        for job in pr2:
            if job not in return_job:
                return_job.append(job)

    elif cross < 1:
        switch_num = random.randint(2, (len(pr1)-1) // 2)
        chosen_indices = sorted(random.sample(range(len(pr1) - 1), k=2 * switch_num))
        tmp = {}

        for idx in range(0, len(chosen_indices), 2):
            st, ed = chosen_indices[idx], chosen_indices[idx + 1]
            for idx2 in range(st, ed):
                tmp[idx2] = pr1[idx2]
        adding = deque()
        for job in pr2:
            if job not in list(tmp.values()):
                adding.append(job)
        for idx in range(len(pr1)):
            if idx in tmp:
                return_job.append(pr1[idx])
            else:
                return_job.append(adding.popleft())
    return tuple(return_job)

def schedule(df):
    dt = Meta(df)
    ini_set = np.array(range(1, len(df) + 1))
    pops = []
    for _ in range(30):
        pops.append(tuple(np.random.permutation(ini_set)))

    tabu_list, pops = {}, sorted(pops, key=lambda gene: dt.total_makespan(gene))
    best, best_score = pops[0], dt.total_makespan(pops[0])
    for _ in range(35):

        mating_pools = []
        for _ in range(40):
            pr1, pr2 = np.random.choice(len(pops), size=2, replace=False)
            if (pr1, pr2) in mating_pools:
                mating_pool_count = 0
                while (pr1, pr2) in mating_pools:
                    pr1, pr2 = np.random.choice(len(pops), size=2, replace=False)
                    if mating_pool_count == 10:
                        break
            mating_pools.append((pr1, pr2))

        offs = []
        for pr1, pr2 in mating_pools:
            pr1, pr2 = pops[pr1], pops[pr2]
            off = crossover(pr1, pr2)
            off = mutation(off)

            if off not in tabu_list:
                offs.append(off)
                tabu_list[off] = 0

        every_gene = pops + offs
        erase_gene = []
        for idx in range(len(every_gene)):
            if idx not in tabu_list:
                tabu_list[every_gene[idx]] = 0
            else:
                tabu_list[every_gene[idx]] += 1
                if tabu_list[every_gene[idx]] == 5:
                    erase_gene.append(idx)

        for gene in reversed(erase_gene):
            tabu_list.pop(gene)
            every_gene.pop(gene)

        every_gene.sort(key=lambda gene: dt.total_makespan(gene))
        candidate_makespan = dt.total_makespan(every_gene[0])
        if candidate_makespan <= dt.total_makespan(best):
            best, best_score = every_gene[0], candidate_makespan
        print(f"Best : {best_score}  /  {best}")
        pops = every_gene.copy()
        if len(pops) <= 1:
            return best
    return best, best_score

def main():
    # week = "sun"   # tue wed thu fri
    # amount = 75000

    scheduling = {}
    for week, amount in (("fri",288000), ()):
        directory = "./ProcessingTimeTable/t_500_20_"
        df = pd.read_csv(directory + week + ".csv")

        total_job = amount//1000 - 1
        df = df.loc[:total_job, :]
        best, best_score = schedule(df)
        scheduling[week] = {"sche": best, "score": best_score}
    print(scheduling)

if __name__ == "__main__":
    main()