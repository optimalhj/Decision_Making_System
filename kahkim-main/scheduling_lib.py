from matplotlib import pyplot as plt
import random
import pandas as pd
# from configs import CONFIGS 

def schedule(ptime_file, job_seq):
    ptimes = pd.read_csv(ptime_file, index_col='JobID', nrows=len(job_seq))
    schedule = build_schedule(job_seq, ptimes)

    # print(schedule)

    makespan = schedule.iloc[-1, -1]
    # print(makespan)

    # gantt 저장
    fig = plot_gantt_chart(schedule, figsize=(50,30), fontsize=22, 
                                outfile=f'gantt_chart.png')

    return makespan


def sort_by_schedule(job_seq, ptimes):
    sorted_ptimes = ptimes.iloc[0:0].copy()
    for jobid in job_seq:
        #  print(ptimes)
         sorted_ptimes.loc[jobid] = ptimes.loc[jobid]
    return sorted_ptimes    

def build_schedule(job_seq, ptimes):
    
    df = sort_by_schedule(job_seq, ptimes).copy()

    # 편의상 df copy
    # df = df_schedule.copy()
    num_machines = df.shape[1]
    
    for i in range(num_machines):
        df[f'M{i+1}_in'] = 0
        df[f'M{i+1}_out'] = 0

    # 첫 time들 설정
    first_job_id = job_seq[0]
    df.loc[first_job_id, 'M1_in'] = 0        
    df.loc[first_job_id, 'M1_out'] = df.loc[first_job_id, 'M1_in'] + ptimes.loc[first_job_id, 'M1']
    for i in range(1, num_machines):
        df.loc[first_job_id, f'M{i+1}_in'] = df.loc[first_job_id, f'M{i}_out']
        df.loc[first_job_id, f'M{i+1}_out'] = df.loc[first_job_id, f'M{i+1}_in'] + ptimes.loc[first_job_id, f'M{i+1}']
    # print(df)
    # iterative하게 반복
    for i in range(1, len(job_seq)):
        job = job_seq[i]
        job_b = job_seq[i-1]
        for j in range(1, num_machines):
            # print(job, job_b, j)
            df.loc[job, 'M1_in'] = df.loc[job_b, 'M1_out']
            df.loc[job, 'M1_out'] = df.loc[job, 'M1_in'] + ptimes.loc[job, 'M1']
            # 첫번째 M2에서의 out 시간이 두번째 M1에서의 in 시간보다 크다면, M2 in 시간은 M2의 전 out 시간이다.
            # 반대로 작거나 같다면, 같은 Job 내의 M2 in 시간은M1 out 시간과 같다.
            if df.loc[job, f'M{j}_out'] < df.loc[job_b, f'M{j+1}_out']:
                df.loc[job, f'M{j+1}_in'] = df.loc[job_b, f'M{j+1}_out']
            elif df.loc[job, f'M{j}_out'] >= df.loc[job_b, f'M{j+1}_out']:
                df.loc[job, f'M{j+1}_in'] = df.loc[job, f'M{j}_out']
            df.loc[job, f'M{j+1}_out'] = df.loc[job, f'M{j+1}_in'] + ptimes.loc[job, f'M{j+1}']                
    
    return df

def plot_gantt_chart(schedule, figsize=(50, 38), fontsize=20, outfile='gantt_chart.png'):
    df = schedule
    num_machines = int(schedule.shape[1]/3)
    plt.figure(figsize=figsize)
    plt.rcParams.update({'font.size': fontsize})
    
    # 랜덤한 색 생성
    colors = []
    for _ in range(500):
        # 랜덤한 RGB 값을 생성하여 colors 리스트에 추가
        colors.append('#{:06x}'.format(random.randint(0, 0xFFFFFF)))

    # Job 번호 범례
    legend_handles = []
    for jobid, _ in df.iterrows():        
        color = colors[jobid - 1]
        legend_handles.append(plt.Rectangle((0, 0), 1, 1, color=color, label=f'Job {jobid}'))

    # 간트 생성
    for _, (jobid, row) in enumerate(df.iterrows()):
        for j in range(num_machines,0,-1):
            M_list = [row[f'M{j}_in'], row[f'M{j}']]
            plt.barh(y=f'Machine {j}', left=M_list[0], width=M_list[1], color=colors[jobid - 1], alpha=0.5)
            plt.text(M_list[0] + M_list[1] / 2, num_machines-j, str(df.loc[jobid, f'M{j}']), ha='center', va='center', color='black')
    
    ticks = list(range(0, num_machines))
    ticks_names = []
    for i in range(num_machines,0,-1):
        ticks_names.append(f'Machine {i}')

    plt.legend(handles=legend_handles, loc='upper left', bbox_to_anchor=(1, 1))
    plt.xlabel('Time')
    plt.ylabel('Machine')
    plt.title('Gantt Chart')
    plt.yticks(ticks, ticks_names)
    plt.savefig(outfile, bbox_inches='tight')
    plt.grid(axis='x')
    # plt.show()

    return plt