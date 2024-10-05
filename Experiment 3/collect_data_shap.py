import importlib
import numpy as np
import pandas as pd
from collections import defaultdict
from tqdm.auto import tqdm
from sklearn.metrics import mean_squared_error, mean_absolute_error, explained_variance_score, median_absolute_error, max_error
from sklearn.model_selection import train_test_split
import concurrent.futures
import shap
import sys
from xgboost import XGBRegressor
import traceback
from utilities import pre_process_data
from statsmodels.stats.stattools import durbin_watson


# SMAPE calculation
def smape(y_true, y_pred):
    return 100 * np.mean(2 * np.abs(y_pred - y_true) / (np.abs(y_pred) + np.abs(y_true)))

# Quantile Loss calculation
def quantile_loss(y_true, y_pred, quantile=0.5):
    residual = y_true - y_pred
    return np.mean(np.maximum(quantile * residual, (quantile - 1) * residual))

# Define models
models = {
    'XGBoost': XGBRegressor(n_estimators=100, max_depth=3, learning_rate=0.1),
}

# Define experiments and their specific important features
experiments = [
    {
        'name': 'Auto Premium',
        'module': 'auto_insurance_premium',
        'prefix': 'auto',
        'important_features': {'Age', 'Gender', 'State', 'Business Use', 'Annual kilometers'}
    },
    {
        'name': 'Cyber Security',
        'prefix': 'cyber',
        'module': 'cybersecurity_insurance_premium',
        'important_features': {'Company Size', 'Industry Risk', 'Security Score', 'Data Sensitivity', 'Business Interruption Cost'}
    },
    {
        'name': 'Environment Liability',
        'prefix': 'env_liab',
        'module': 'env_liability_insurance_premium',
        'important_features': {'Industry Type', 'Company Size', 'Pollution Risk', 'Regulatory Compliance', 'Years of Operation', 'Incident History', 'Coverage Limit'}
    }
]

target_column = 'Premium'
experiment_file_name = "experiment_H"
N = 100
sample_sizes = [12000, 42000, 55000, 66000, 120000]
experiments_to_run = ['Auto Premium', 'Cyber Security', 'Environment Liability']

# Function to run models
def run_model(name, model, X_train, X_test, y_train, y_test, X_processed, important_features):
    try:
        model.fit(X_train, y_train)
        y_pred = model.predict(X_test)
        r2_score = model.score(X_test, y_test)
        mse_score = mean_squared_error(y_test, y_pred)
        mae_score = mean_absolute_error(y_test, y_pred)
        rmse_score = np.sqrt(mse_score)
        accuracy_score = explained_variance_score(y_test, y_pred)
        medae = median_absolute_error(y_test, y_pred)
        max_err = max_error(y_test, y_pred)
        rae = np.sum(np.abs(y_test - y_pred)) / np.sum(np.abs(y_test - np.mean(y_test)))
        smape_value = smape(y_test, y_pred)
        bias = np.mean(y_pred - y_test)
        interval = 1.96 * np.std(y_pred)
        lower_bound = y_pred - interval
        upper_bound = y_pred + interval
        picp = np.mean((y_test >= lower_bound) & (y_test <= upper_bound))
        cv = np.std(y_pred) / np.mean(y_pred)
        quantile_loss_value = quantile_loss(y_test, y_pred, quantile=0.5)
        residuals = y_test - y_pred
        durbin_watson_stat = durbin_watson(residuals)
        explainer = shap.TreeExplainer(model, feature_perturbation='interventional')
        shap_values = explainer.shap_values(X_test, check_additivity=False)
        shap_feature_importance = dict(zip(X_processed.columns, np.mean(np.abs(shap_values), axis=0)))
        sorted_features = sorted(shap_feature_importance.items(), key=lambda item: item[1], reverse=True)
        sorted_feature_names = [feature for feature, importance in sorted_features]
        top_features = set(sorted_feature_names[:len(important_features)])
        match_percentage = len(important_features.intersection(top_features)) / len(important_features) * 100

        return {
            'Model': name,
            'Sample Size': len(X_train),
            'R²': r2_score,
            'MAE': mae_score,
            'RMSE': rmse_score,
            'Accuracy': accuracy_score,
            'MedAE': medae,
            'Max Error': max_err,
            'RAE': rae,
            'sMAPE': smape_value,
            'Bias': bias,
            'PICP': picp,
            'CV': cv,
            'Quantile Loss': quantile_loss_value,
            'Durbin-Watson': durbin_watson_stat,
            'SHAP Match Percentage': match_percentage,
            'Top SHAP Features': {feature: importance for feature, importance in sorted_features[:len(important_features)]}
        }
    except Exception as e:
        traceback.print_exc()
        return None

# Main function
def main():
    overall_results = defaultdict(lambda: defaultdict(lambda: {'matches': 0, 'R²': [], 'MAE': [], 'RMSE': [], 'Accuracy': [], 'Explained Variance': [], 'MedAE': [], 'Max Error': [], 'RAE': [], 'MASE': [], 'sMAPE': [], 'Bias': [], 'PICP': [], 'CV': [], 'Quantile Loss': [], 'Durbin-Watson': []}))

    for experiment in experiments:
        if experiment['name'] in experiments_to_run:
            experiment_name = experiment['name']
            experiment_module_name = experiment['module']
            experiment_prefix = experiment['prefix']
            important_features = experiment['important_features']

            experiment_module = importlib.import_module(experiment_module_name)
            generate_test_data = getattr(experiment_module, 'generate_test_data')

            results_table = []
            exp_index = 1
            total_iterations = N * len(sample_sizes) * len(models)

            with tqdm(total=total_iterations, desc=f"Running {experiment_name}", leave=True) as pbar:
                with concurrent.futures.ProcessPoolExecutor() as data_executor:
                    for iteration in range(N):
                        # Generate data concurrently
                        data_futures = [data_executor.submit(generate_test_data, size) for size in sample_sizes]

                        for size, data_future in zip(sample_sizes, data_futures):
                            try:
                                data = data_future.result()
                                X_processed, y, mappings = pre_process_data(data, target_column=target_column)
                                X_train, X_test, y_train, y_test = train_test_split(X_processed, y, test_size=0.2, random_state=7)

                                # Use ThreadPoolExecutor to parallelize model evaluation
                                with concurrent.futures.ThreadPoolExecutor() as model_executor:
                                    futures = [model_executor.submit(run_model, model_name, model, X_train, X_test, y_train, y_test, X_processed, important_features) for model_name, model in models.items()]

                                    for future in concurrent.futures.as_completed(futures):
                                        result = future.result()
                                        if result is not None:
                                            results_table.append(result)
                                            exp_index += 1
                                            pbar.update(1)

                            except Exception as e:
                                tqdm.write(f"Error during processing at sample size {size}: {e}")
                                continue

            raw_results_df = pd.DataFrame(results_table)
            csv_filename = f"{experiment_prefix}_{experiment_file_name}.csv"
            raw_results_df.to_csv(csv_filename, index=False)
            print(f"Results for {experiment_name} saved to {csv_filename}")

if __name__ == '__main__':
    main()