from __future__ import print_function
import csv
import numpy as np
import tensorflow as tf
from utils.load_data import DataLoad

flags = tf.app.flags
flags.DEFINE_integer("width", 128, "width")
flags.DEFINE_integer("height", 128, "height")
flags.DEFINE_integer("layers", 128, "layers")
flags.DEFINE_integer("batch_size", 32, "batch size")
flags.DEFINE_integer("num_process", 1, "process number")
flags.DEFINE_bool("is_train", True, "is train")
flags.DEFINE_string("data_type", "stage1", "sample or stage1")
config = flags.FLAGS

def expand_last_dim(*input_data):
  res = []
  for in_data in input_data:
    res.append(np.expand_dims(in_data, axis=len(in_data.shape)))
  if len(res) == 1:
    return res[0]
  else:
    return res

def conv3d(input_data, w, stride):
  return tf.nn.conv3d(input_data, w, strides=stride, padding='SAME')

def max_pool3d(input_data, depth_stride):
  return tf.nn.max_pool3d(input_data, [1, depth_stride, 2, 2, 1], [1, depth_stride, 2, 2, 1], padding='SAME')

# Parameters
in_depth = config.layers
in_height = config.height
in_width = config.width
in_channels = 1

filter_depth = 3
filter_height = 3
filter_width = 3
conv_stride = [1, 1, 1, 1, 1]

layer11_channels = 2
layer12_channels = 4
layer2_channels = 8

num_hidden = 64
num_labels = 1

# Graph
with tf.device('/gpu:0'):
  # Input data.
  is_training = tf.placeholder(tf.bool)
  tf_dataset = tf.placeholder(
    tf.float32, [None, in_depth, in_height, in_width, in_channels])
  tf_labels = tf.placeholder(tf.float32, [None, num_labels])

  # Variables.
  layer11_weights = tf.Variable(tf.truncated_normal(
    [filter_depth, filter_height, filter_width, in_channels, layer11_channels], stddev=1.0))
  layer11_biases = tf.Variable(tf.zeros([layer11_channels]))

  layer12_weights = tf.Variable(tf.truncated_normal(
    [filter_depth, filter_height, filter_width, layer11_channels, layer12_channels], stddev=1.0))
  layer12_biases = tf.Variable(tf.zeros([layer12_channels]))

  layer2_weights = tf.Variable(tf.truncated_normal(
    [filter_depth, filter_height, filter_width, layer12_channels, layer2_channels], stddev=1.0))
  layer2_biases = tf.Variable(tf.constant(0.0, shape=[layer2_channels]))

  layer3_weights = tf.Variable(tf.truncated_normal(
    [in_depth // 4 * in_height // 4 * in_width // 4 * layer2_channels, num_hidden], stddev=1.0))
  layer3_biases = tf.Variable(tf.constant(0.0, shape=[num_hidden]))

  layer4_weights = tf.Variable(tf.truncated_normal(
    [num_hidden, num_labels], stddev=0.5))
  layer4_biases = tf.Variable(tf.constant(0.0, shape=[num_labels]))


  # Model.
  def model(data, phase):
    # normalize
    max_v = tf.reduce_max(data)
    data = tf.realdiv(data, max_v)

    conv11 = conv3d(data, layer11_weights, conv_stride)
    conv11 = tf.contrib.layers.batch_norm(conv11, is_training=phase)
    conv11 = tf.nn.relu(conv11 + layer11_biases)
    conv12 = conv3d(conv11, layer12_weights, conv_stride)
    conv12 = tf.contrib.layers.batch_norm(conv12, is_training=phase)
    conv12 = tf.nn.relu(conv12 + layer12_biases)
    pool1 = max_pool3d(conv12, 2)

    conv2 = conv3d(pool1, layer2_weights, conv_stride)
    conv2 = tf.contrib.layers.batch_norm(conv2, is_training=phase)
    conv2 = tf.nn.relu(conv2 + layer2_biases)
    pool2 = max_pool3d(conv2, 2)

    shape = pool2.get_shape().as_list()
    reshape = tf.reshape(pool2, [tf.shape(data)[0], shape[1] * shape[2] * shape[3] * shape[4]])

    hidden = tf.matmul(reshape, layer3_weights) + layer3_biases
    hidden = tf.contrib.layers.batch_norm(hidden, is_training=phase)
    hidden = tf.nn.relu(hidden)
    return tf.matmul(hidden, layer4_weights) + layer4_biases

  logits = model(tf_dataset, is_training)
  # Prediction
  loss = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits, tf_labels))

  # Optimizer.
  optimizer = tf.train.AdamOptimizer(learning_rate=0.03, beta1=0.5)
  grads = optimizer.compute_gradients(loss)
# capped_gvs = [(tf.clip_by_value(grad, -1., 1.), var) for grad, var in grads]
  update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
  with tf.control_dependencies(update_ops):
    # Ensures that we execute the update_ops before performing the train_step, for batch_norm
    train_op = optimizer.apply_gradients(grads)

  # Prediction
  prediction = tf.sigmoid(logits)


# Training
num_epochs = 30
sess_config = tf.ConfigProto()
sess_config.gpu_options.allow_growth = True
sess_config.log_device_placement=False
sess_config.allow_soft_placement=True

with tf.Session(config=sess_config) as session:
  tf.global_variables_initializer().run()
  print('Initialized')
  
  data_loader = DataLoad(config=config)
  f = open('loss.log', 'w')
  for epoch in range(num_epochs):
    # Training
    data_loader.train(equal_distribution=True)
    while data_loader.has_next_batch():
      train_data, train_label, _ = data_loader.next_batch()
      # train_data, train_label = sample_equally(np.array(train_data), np.array(train_label))
      train_data, train_label = expand_last_dim(train_data, train_label)
      print(train_data.shape)

      feed_dict = {tf_dataset: train_data, tf_labels: train_label, is_training: True}
      _, l, preds = session.run([train_op, loss, prediction], feed_dict=feed_dict)
      print('labels: preds \n %s' % np.concatenate((train_label, preds), axis=1))
      print('Mini-batch loss: %f' % l)
      f.write('train: %f\n' % l)
      f.flush()

    # Validation
    data_loader.validation()
    total_loss = 0
    count = 0
    while data_loader.has_next_batch():
      valid_data, valid_label, _ = data_loader.next_batch()
      valid_data, valid_label = expand_last_dim(valid_data, valid_label)

      feed_dict = {tf_dataset: valid_data, tf_labels: valid_label, is_training: False}
      l = session.run(loss, feed_dict=feed_dict)
      batch_size = valid_data.shape[0]
      total_loss = total_loss + l * batch_size
      count = count + batch_size

    valid_loss = total_loss / count
    print('Validation loss is: %f', valid_loss)
    f.write('valid: %f\n' %  l)
    f.flush()

  f.close()
  # Test predictions
  data_loader.test()
  pred_dict = {}
  while data_loader.has_next_batch():
    test_data, _, test_id = data_loader.next_batch()
    test_data = expand_last_dim(test_data)
    
    feed_dict = {tf_dataset : test_data, is_training: False}
    preds = session.run(prediction, feed_dict=feed_dict)
    for i in range(test_data.shape[0]):
      pred_dict[test_id[i]] = preds[i][0]

  print("Save submission to submission_backup.csv")
  with open('submission_backup.csv', 'w') as f:
    writer = csv.writer(f)
    # write the header
    for row in {'id':'cancer'}.items():
      writer.writerow(row)
    # write the content
    for row in pred_dict.items():
      writer.writerow(row)
