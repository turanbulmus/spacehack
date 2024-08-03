import gdown 
import random, time, requests, os, json, base64
from pathlib import Path

import pandas as pd
import numpy as np
from matplotlib import pyplot as plt
from sklearn.metrics import confusion_matrix, ConfusionMatrixDisplay

from vertexai.generative_models import GenerativeModel, Part, FinishReason, Image
import vertexai.preview.generative_models as generative_models
from google.cloud import bigquery
from google.cloud.exceptions import NotFound
import google.cloud.aiplatform as aiplatform

def generate(model, prompt):
  """Generates text based on the provided prompt using a Gemini model.

  Args:
    prompt: The text prompt to use for generating text.

  Returns:
    A list of responses generated by the model.

  Raises:
    Exception: If there is an error generating content.
  """
  # Generate content using the specified prompt and configurations.
  responses = model.generate_content(
      prompt,
      generation_config=generation_config,# Configuration for text generation
      safety_settings=safety_settings,# Settings for safety checks during generation
      stream=False,# Set to False to retrieve all generated content at once
  )
  # Return the generated responses.
  return responses

def create_ex(data_index):
  """
    Loads and returns a list containing strings and images to be used for Gemini for a given data index.

    Args:
        data_index (int): The index of the data set to load.

    Returns:
        list: A list containing strings and images. The list contains:
            - "new image: "
            - Image object loaded from "data/pics/Example_{data_index}_fig_0.png"
            - "reference image: "
            - Image object loaded from "data/pics/Example_{data_index}_fig_1.png"
            - "difference image: "
            - Image object loaded from "data/pics/Example_{data_index}_fig_2.png"
    """   
  str_new = "new image: " # String for labeling the new image
  str_ref = "reference image: " # String for labeling the reference image
  str_dif = "difference image: " # String for labeling the difference image

  # Load images from files using the given data index
  image1 = Part.from_image(Image.load_from_file(f"data/pics/Example_{data_index}_fig_0.png"))
  image2 = Part.from_image(Image.load_from_file(f"data/pics/Example_{data_index}_fig_1.png"))
  image3 = Part.from_image(Image.load_from_file(f"data/pics/Example_{data_index}_fig_2.png"))
  
  # Return the list containing strings and images
  return [str_new, image1, str_ref, image2, str_dif, image3]

def preprocess(dataset, index_no):
  """Preprocesses a pair of images from a dataset for analysis.
  This function takes a multi-dimensional array containing image data, a corresponding array of labels, and an index. It then:

  1. **Combines** the real and reference images at the specified index.
  2. **Normalizes** the combined image to a range of 0-255.
  3. **Separates** the normalized image back into real and reference images.
  4. **Calculates** the difference image between the reference and real images.
  5. **Returns** the real image, reference image, difference image, and the label associated with the index.

  Args:
    dataset: A multi-dimensional array containing image data. Each element is expected to be a 4-dimensional array representing a pair of images (real and reference).
    index_no: The index of the image pair to be processed.

  Returns:
    A tuple containing:
      - real_image: A 2D array representing the real image.
      - ref_image: A 2D array representing the reference image.
      - diff_image: A 2D array representing the difference between the reference and real images.
      - label: The label associated with the processed image pair.
  """
  # Combine the real image and the reference image
  comb_im = np.append(dataset[index_no, 0:100, 0:100, 0], dataset[index_no, 0:100, 0:100, 1])
  # Normalize the combined image with clipping to 2% and 98%
  p2 = np.percentile(comb_im, 2)
  p98 = np.percentile(comb_im, 98)
  comb_im[comb_im < p2] = p2
  comb_im[comb_im > p98] = p98
  norm_comb_im = 255 * (comb_im - p2) / (p98 - p2)

  # Separate the array back
  real_image = np.reshape(norm_comb_im[0:100*100], (100, 100))
  ref_image = np.reshape(norm_comb_im[100*100:], (100, 100))

  # Rescale the already available difference image
  diff_image = np.copy(dataset[index_no, :, :, 2])  # Create a copy to avoid modifying the original data
  p2_diff = np.percentile(diff_image, 2)
  p98_diff = np.percentile(diff_image, 98)
  diff_image[diff_image < p2_diff] = p2_diff
  diff_image[diff_image > p98_diff] = p98_diff
  norm_diff_image = 255 * (diff_image - p2_diff) / (p98_diff - p2_diff)
  # Return the real image, the reference image and the difference image
  return [real_image, ref_image, norm_diff_image]

def save_picture(dataset, index_no):
  # Save the images as png
  processed_im = preprocess(dataset, index_no)
  for j in range(3):
      plt.imsave(f"data/pics/Example_{index_no}_fig_{j}.png", processed_im[j],cmap='gray')
      
def save_prompt(instructions, run_name):
  """Saves the system instructions to a text file. It first strips the images from the system prompt and only saves the text part of the prompt to save space.

  Args:
    instructions: A list of strings representing the system instructions.
    run_name: The name of the experiment that will test the prompt. The prompt will be saved in "prompts/prompt_{run_name}.txt".
  """
  with open("prompts/prompt_" + run_name + ".txt", "a") as f:
    f.write("".join(instructions) + "\n")
  return "prompt_" + run_name + ".txt"
    
def build_experiment_vars(**kwargs):
  """
  This function takes any number of keyword arguments and returns a dictionary 
  where the keys are the argument names and the values are the argument values.
  It is meant to build experiment variables
  Args:
    **kwargs: Keyword arguments.

  Returns:
    A dictionary with the argument names as keys and the argument values as values.
  """
  return kwargs

def create_batch_prediction_job(project_id, request_json_path):
  """
  Sends a POST request to the Google Cloud AI Platform Batch Prediction API.

  Args:
    project_id: The Google Cloud Project ID.
    request_json_path: The path to the JSON file containing the batch prediction request.

  Returns:
    The response from the API call.
  """

  # Get the access token from gcloud
  access_token = os.popen('gcloud auth print-access-token').read().strip()

  # Construct the API endpoint URL
  url = f"https://us-central1-aiplatform.googleapis.com/v1/projects/{project_id}/locations/us-central1/batchPredictionJobs"

  # Read the request JSON file
  with open(request_json_path, 'r') as f:
    request_data = json.load(f)

  # Set the headers
  headers = {
    "Authorization": f"Bearer {access_token}",
    "Content-Type": "application/json; charset=utf-8"
  }

  # Send the POST request
  response = requests.post(url, headers=headers, json=request_data)

  return response.json()

def write_request(name, model, inputUri, outputUri):
  """Writes a JSON file containing a batch prediction request to Google Cloud AI Platform.

  This function creates a JSON file named "request.json" with the specified parameters for a batch prediction job.
  The JSON file follows the schema required by the Google Cloud AI Platform Batch Prediction API.

  Args:
    name: The name of the batch prediction job.
    model: The name of the model to use for batch prediction.
    inputUri: The BigQuery URI of the input data for batch prediction.
    outputUri: The BigQuery URI of the output data for batch prediction.
  """
  with open("request.json", 'w') as f:
    json.dump({
        "displayName": name,
        "model": "publishers/google/models/" + model,
        "inputConfig": {
          "instancesFormat":"bigquery",
          "bigquerySource":{
            "inputUri" : inputUri
          }
        },
        "outputConfig": {
          "predictionsFormat":"bigquery",
          "bigqueryDestination":{
            "outputUri": outputUri
          }
        }
    }, f, indent=4)

def if_tbl_exists(bq_client, table_ref):
    """Checks if a table exists in BigQuery and creates it if it doesn't.

  Args:
      bq_client: A BigQuery client object.
      table_ref: A BigQuery table reference object.

  Returns:
      True if the table exists, False otherwise.
  """
    try:
        bq_client.get_table(table_ref)
        return True
    except NotFound:
        return bq_client.create_table(table_ref)

def batch_data_create(stat_prompt, dyna_prompt, TEMPERATURE, TOP_P):
  """
  Creates a JSON payload for batch data generation with OpenAI API.

  Args:
    stat_prompt: Static prompt that will be used for all data points in the batch.
    dyna_prompt: Dynamic prompt, can be a list of strings or dictionaries. If a dictionary, it must be
                 a valid OpenAI API prompt structure.
    TEMPERATURE: Temperature parameter for text generation.
    TOP_P: Top P parameter for text generation.
    TOP_K: Top K parameter for text generation.

  Returns:
    A JSON string representing the batch data request payload.
  """
  dyna_prompt_part = []
  for i in range(len(dyna_prompt)):
    if type(dyna_prompt[i]) == str:
      dyna_prompt_part.append({"text": dyna_prompt[i]})
    else:
      dyna_prompt_part.append(dyna_prompt[i].to_dict())
  
  return  json.dumps(
    {
    "contents": [
      {
        "role": "user",
        "parts": dyna_prompt_part
      }
    ],
    "system_instruction": {
      "parts": [
        {
          "text": stat_prompt
        }
      ]
    },
    "generationConfig": {
        "maxOutputTokens": 2024,
        "temperature": TEMPERATURE,
        "topP": TOP_P,
        "responseMimeType": "application/json",
    },
    "safetySettings": [
        {
            "category": "HARM_CATEGORY_HATE_SPEECH",
            "threshold": "BLOCK_NONE"
        },
        {
            "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
            "threshold": "BLOCK_NONE"
        },
        {
            "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "threshold": "BLOCK_NONE"
        },
        {
            "category": "HARM_CATEGORY_HARASSMENT",
            "threshold": "BLOCK_NONE"
        }
    ],
  })

  from google.cloud import bigquery

def build_run_batch(bq_client, batch_index, labels_ref, PROJECT_ID, DATASET_ID, run_name, model, stat_prompt, examples, temperature, top_p):
  """Builds necessary the batch request job, run the batch process job and returns the results.

  This function performs the following steps:
    1. Constructs input and output table names based on the project ID and formatted datetime.
    2. Defines the table schema with 'request' (JSON) and 'index_no' (INTEGER) fields.
    3. Creates the input table in BigQuery if it doesn't exist.
    4. For index item in the batch_index:
       - Constructs a dynamic prompt using the provided examples and the current index.
       - Creates a batch data dataframe using the static prompt, dynamic prompt, and specified parameters.
       - Uploads the dataframe to a GCS bucket
       - Creates a Big query table using the data stored in GCS bucket. 
    6. Generates a request.json file for batch processing.
    7. Sends the batch prediction job to the specified project.
    8. Waits until the batch prediction job concludes.
    9. Generate a Big Query table that processes the BatchPredictionJob
    10. Download the table and exports as a pandas dataframe

  Args:
    bq_client: An instance of the BigQuery client.
    PROJECT_ID: The ID of the Google Cloud project.
    batch_index: The list that stores all the saved pictures(including real, reference and difference)
    labels_ref: Reference to the Big Query table that holds the ground truth information 
    stat_prompt: The static prompt to use for all requests.
    run_name: The name of the Vertex AI Experiment run. 
    examples: A set of examples to use for constructing dynamic prompts.
    temperature: The temperature parameter for the generative model.
    top_p: The top_p parameter for the generative model.
    top_k: The top_k parameter for the generative model.
    random_seed: An optional seed for the random number generator.

  Returns:
    A pandas dataframe with processed results including ground truth.
  """
  # Construct table names
  input_table_name = f'{PROJECT_ID}.{DATASET_ID}.input'
  output_table_name = f'{PROJECT_ID}.{DATASET_ID}.output'

  # Define the table schema
  schema = [
      bigquery.SchemaField('request', 'JSON'),
      bigquery.SchemaField('index_no', 'INTEGER')
  ]
  
  # Create the table if it doesnt exist
  table = bigquery.Table(input_table_name, schema=schema)
  if_tbl_exists(bq_client, table)
  
  # Create the pandas df that stores the requests
  batch_df = pd.DataFrame(columns=["request", "index_no"])

  for t in batch_index:
    dyna_prompt = examples + create_ex(t)
    df_temp = pd.DataFrame([[batch_data_create(stat_prompt, dyna_prompt, temperature, top_p), t]], columns=["request", "index_no"])
    batch_df = pd.concat([batch_df, df_temp], ignore_index=True)
  
  job_config = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
  job_config.source_format = 'CSV'

  job = bq_client.load_table_from_dataframe(
      batch_df, input_table_name, job_config=job_config
  )  # Make an API request.
  job.result()  # Wait for the job to complete.
 
  # Generate the request.json for batch processing
  write_request("spacehackbatch_check", model, "bq://" + input_table_name,
                "bq://" + output_table_name)

  # Send the batch response
  response = create_batch_prediction_job(PROJECT_ID, "request.json")
  # Run the batch process job and wait for completion.
  job = aiplatform.BatchPredictionJob(response["name"].split("/")[-1])
  job.wait_for_completion()

  # The query to generate a final table with results
  create_table_query = f"""
  CREATE OR REPLACE TABLE `{PROJECT_ID}.{DATASET_ID}.{run_name}` AS
  SELECT  t1.index_no, t2.label AS actual,
      JSON_VALUE(JSON_VALUE(REGEXP_REPLACE(t1.response, r'^\[(.*)\]$', r'\\1'), '$.content.parts[0].text'),'$.class') AS predicted,
      JSON_VALUE(JSON_VALUE(REGEXP_REPLACE(t1.response, r'^\[(.*)\]$', r'\\1'), '$.content.parts[0].text'),'$.explanation') AS explanation,
      t1.response, t1.request 
          FROM `{output_table_name}` as t1
    LEFT JOIN `{PROJECT_ID}.{DATASET_ID}.{labels_ref.table_id}` as t2 
    ON t1.index_no=t2.index_no"""
  # Run the query
  query_job = bq_client.query(create_table_query)
  results = query_job.result()
  # Delete the interim tables
  bq_client.delete_table(output_table_name, not_found_ok=True)  # Make an API request.
  bq_client.delete_table(input_table_name, not_found_ok=True)  # Make an API request.
  
  # Download the results to generate KPIs
  download_query = f"""
  SELECT index_no, actual, predicted, explanation
  FROM {PROJECT_ID}.{DATASET_ID}.{run_name} 
  """
  return bq_client.query_and_wait(download_query).to_dataframe()